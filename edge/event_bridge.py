"""Edge event bridge — routes Session_Manager / light events to the Sync_Client.

On the Edge_Agent, the ``PipelineOrchestrator`` pipeline loop already produces,
per processed frame, a ``SessionManager.process_frame()`` **snapshot** dict that
carries an ``events`` list (``session_opened``, ``session_closed``,
``alert_generated``) plus, alongside it, a ``LightDetector.detect()`` result
describing tower-light transitions. In the monolith these events were consumed
locally; on the edge they must instead be forwarded to the Cloud_Server.

:class:`EventBridge` is the thin adapter that does exactly that. The edge loop
(task 15.1) invokes :meth:`EventBridge.process` once per frame with the snapshot
and the frame, and the bridge routes each event to the matching durable
``Sync_Client.submit_*`` method:

===========================  ==================================
Event                        Sync_Client method
===========================  ==================================
``session_opened``           :meth:`SyncClient.submit_session` (op=open)
``session_closed``           :meth:`SyncClient.submit_session` (op=close)
``alert_generated``          :meth:`SyncClient.submit_alert` (with Event_Image)
machine light transition     :meth:`SyncClient.submit_machine_event`
===========================  ==================================

Two guarantees this module upholds:

- **No credentials leave the edge.** Every outbound payload is built from the
  credential-free ``api.ingest_schemas`` models using only session/alert/light
  fields; RTSP URLs and camera credentials are never read here, so they cannot
  appear in any transmitted payload (Requirements 7.7, 13.4). A defensive
  scrub asserts this invariant before submission.
- **Sessions collapse to one cloud row.** The bridge assigns a stable
  ``session_uuid`` on ``session_opened`` and reuses it for the matching
  ``session_closed`` so the cloud upserts a single Session_Record. A close with
  no preceding open still gets a fresh ``session_uuid`` (the cloud creates a
  closed record — the orphan-close path).

Requirements: 1.2, 8.1, 7.7, 13.4
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import structlog

from api.ingest_schemas import AlertMsg, MachineEventMsg, SessionRecordMsg

logger = structlog.get_logger(__name__)

# Optional OpenCV — used to annotate + JPEG-encode the alert Event_Image. The
# import is guarded so the bridge stays importable (and event routing keeps
# working) on hosts without OpenCV; alerts then carry an empty image.
try:  # pragma: no cover - availability depends on the host
    import cv2  # type: ignore

    _CV2_AVAILABLE = True
except Exception:  # pragma: no cover - defensive
    cv2 = None  # type: ignore
    _CV2_AVAILABLE = False

# Fields that must never appear in an outbound payload (Requirements 7.7, 13.4).
_FORBIDDEN_KEYS = ("rtsp", "password", "credential", "username")

# Valid tower-light classifications (mirrors the LightState literal).
_LIGHT_STATES = frozenset({"GREEN", "AMBER", "RED", "OFF", "UNKNOWN"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _assert_credential_free(payload: Dict[str, Any]) -> None:
    """Fail loudly if a payload key names an RTSP URL or camera credential.

    A last-line-of-defense check enforcing Requirements 7.7 / 13.4. Because the
    bridge only ever constructs payloads from credential-free schema models,
    this never trips in practice — it guards against a future field being added
    that would leak a secret.
    """
    for key in payload:
        lowered = key.lower()
        for forbidden in _FORBIDDEN_KEYS:
            if forbidden in lowered:
                raise ValueError(
                    f"outbound payload contains forbidden field {key!r} "
                    f"(Requirements 7.7, 13.4)"
                )


class EventBridge:
    """Routes Session_Manager and light events to the Sync_Client.

    Parameters
    ----------
    sync_client:
        The :class:`~edge.sync_client.SyncClient` durable events are submitted
        to. Only its ``submit_session`` / ``submit_alert`` /
        ``submit_machine_event`` methods are used.
    machine_id:
        Fallback machine ID used when an event does not carry its own
        ``machine_id`` (e.g. a ``SessionManager`` constructed without one).
    clock:
        Callable returning the current ``datetime`` (injectable for tests).
        Defaults to timezone-aware UTC now.
    image_encoder:
        Optional callable ``(frame, meta) -> bytes`` overriding the default
        OpenCV annotate-and-encode used to build the alert Event_Image.
    jpeg_quality:
        JPEG quality (0-100) for the default Event_Image encoder.
    """

    def __init__(
        self,
        sync_client,
        *,
        machine_id: Optional[str] = None,
        clock: Callable[[], datetime] = _utcnow,
        image_encoder: Optional[Callable[[Any, Dict[str, Any]], bytes]] = None,
        jpeg_quality: int = 80,
    ) -> None:
        self._sync = sync_client
        self._machine_id = machine_id
        self._clock = clock
        self._image_encoder = image_encoder
        self._jpeg_quality = int(jpeg_quality)
        # machine_id -> the session_uuid of the currently open session.
        self._open_session_uuids: Dict[str, str] = {}

    # ── Entry point invoked by the edge loop ──────────────
    def process(
        self,
        snapshot: Dict[str, Any],
        frame: Any = None,
        light_result: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Route every event in ``snapshot`` (and any light transition).

        Consumes ``snapshot['events']`` (the list produced by
        ``SessionManager.process_frame``) and, when ``light_result`` reports a
        transition, submits a Machine_Event. Returns the list of assigned
        ``event_id`` strings, in submission order.

        The ``frame`` is only used to capture an annotated Event_Image for
        ``alert_generated`` events (Requirement 8.1).
        """
        event_ids: List[str] = []

        for event in snapshot.get("events", []) or []:
            event_id = self._route_event(event, snapshot, frame)
            if event_id is not None:
                event_ids.append(event_id)

        if light_result is not None:
            light_id = self.process_light_result(light_result, snapshot)
            if light_id is not None:
                event_ids.append(light_id)

        return event_ids

    def _route_event(
        self, event: Dict[str, Any], snapshot: Dict[str, Any], frame: Any
    ) -> Optional[str]:
        etype = event.get("type")
        if etype == "session_opened":
            return self._submit_session_open(event, snapshot)
        if etype == "session_closed":
            return self._submit_session_close(event, snapshot)
        if etype == "alert_generated":
            return self._submit_alert(event, snapshot, frame)
        if etype in ("machine_light_transition", "light_transition"):
            return self.process_light_result(event, snapshot)
        logger.debug("EventBridge ignoring unknown event type", event_type=etype)
        return None

    # ── Machine ID resolution ─────────────────────────────
    def _resolve_machine_id(self, *sources: Dict[str, Any]) -> str:
        for src in sources:
            mid = src.get("machine_id")
            if mid:
                return mid
        if self._machine_id:
            return self._machine_id
        raise ValueError(
            "EventBridge could not resolve a machine_id for an event; "
            "construct the SessionManager with a machine_id or pass one to "
            "EventBridge"
        )

    # ── Session events ────────────────────────────────────
    def _submit_session_open(
        self, event: Dict[str, Any], snapshot: Dict[str, Any]
    ) -> str:
        machine_id = self._resolve_machine_id(event, snapshot)
        session_uuid = str(uuid.uuid4())
        self._open_session_uuids[machine_id] = session_uuid

        msg = SessionRecordMsg(
            event_id="",  # assigned by the Sync_Client on enqueue
            machine_id=machine_id,
            session_uuid=session_uuid,
            produced_at=self._clock(),
            op="open",
            start_time=event.get("start_time") or self._clock(),
            end_time=None,
            active_duration_seconds=0.0,
            close_reason=None,
        )
        _assert_credential_free(msg.model_dump(mode="json"))
        event_id = self._sync.submit_session(msg)
        logger.debug(
            "EventBridge forwarded session_opened",
            machine_id=machine_id,
            session_uuid=session_uuid,
            event_id=event_id,
        )
        return event_id

    def _submit_session_close(
        self, event: Dict[str, Any], snapshot: Dict[str, Any]
    ) -> str:
        machine_id = self._resolve_machine_id(event, snapshot)
        # Reuse the open session's uuid so the cloud upserts one row; if there
        # was no matching open (orphan close) assign a fresh uuid — the cloud
        # then creates a closed record (Requirement 5.7).
        session_uuid = self._open_session_uuids.pop(machine_id, None)
        if session_uuid is None:
            session_uuid = str(uuid.uuid4())
            logger.info(
                "EventBridge close with no open session — orphan close",
                machine_id=machine_id,
                session_uuid=session_uuid,
            )

        msg = SessionRecordMsg(
            event_id="",
            machine_id=machine_id,
            session_uuid=session_uuid,
            produced_at=self._clock(),
            op="close",
            start_time=event.get("start_time") or self._clock(),
            end_time=event.get("end_time") or self._clock(),
            active_duration_seconds=float(
                event.get("active_duration_seconds", 0.0) or 0.0
            ),
            close_reason=event.get("close_reason"),
        )
        _assert_credential_free(msg.model_dump(mode="json"))
        event_id = self._sync.submit_session(msg)
        logger.debug(
            "EventBridge forwarded session_closed",
            machine_id=machine_id,
            session_uuid=session_uuid,
            event_id=event_id,
        )
        return event_id

    # ── Alert events ──────────────────────────────────────
    def _submit_alert(
        self, event: Dict[str, Any], snapshot: Dict[str, Any], frame: Any
    ) -> str:
        machine_id = self._resolve_machine_id(event, snapshot)
        produced_at = event.get("timestamp") or self._clock()

        meta = {
            "machine_id": machine_id,
            "alert_type": event.get("alert_type", "unknown"),
            "message": event.get("message"),
            "timestamp": produced_at,
        }
        image_bytes = self._capture_event_image(frame, meta)

        msg = AlertMsg(
            event_id="",
            machine_id=machine_id,
            produced_at=produced_at if isinstance(produced_at, datetime) else self._clock(),
            alert_type=event.get("alert_type", "unknown"),
            message=event.get("message"),
            event_image_b64="",  # populated by submit_alert from image_bytes
        )
        _assert_credential_free(msg.model_dump(mode="json", exclude={"event_image_b64"}))
        event_id = self._sync.submit_alert(msg, image_bytes)
        logger.debug(
            "EventBridge forwarded alert_generated",
            machine_id=machine_id,
            alert_type=meta["alert_type"],
            image_bytes=len(image_bytes),
            event_id=event_id,
        )
        return event_id

    def _capture_event_image(self, frame: Any, meta: Dict[str, Any]) -> bytes:
        """Capture an annotated Event_Image for an alert (Requirement 8.1).

        Uses a caller-provided ``image_encoder`` if given, otherwise the default
        OpenCV annotate-and-encode. Returns ``b""`` when no frame is available
        or OpenCV is missing — the alert is still delivered (image-less).
        """
        if self._image_encoder is not None:
            try:
                return self._image_encoder(frame, meta) or b""
            except Exception as exc:  # noqa: BLE001 - never fail the alert
                logger.warning("Custom Event_Image encoder failed", error=str(exc))
                return b""

        if frame is None or not _CV2_AVAILABLE:
            return b""
        try:
            return _annotate_and_encode(frame, meta, self._jpeg_quality)
        except Exception as exc:  # noqa: BLE001 - never fail the alert
            logger.warning("Event_Image annotate/encode failed", error=str(exc))
            return b""

    # ── Machine light transition ──────────────────────────
    def process_light_result(
        self, light_result: Dict[str, Any], snapshot: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Submit a Machine_Event when the light detector reports a transition.

        Accepts a ``LightDetector.detect()`` result (``status``/``transition``/
        ``previous``) or an equivalent event dict (``new_status``/
        ``previous_status``). Returns the assigned ``event_id`` or ``None`` when
        there is no transition to forward.
        """
        transition = light_result.get("transition")
        new_status = light_result.get("new_status") or light_result.get("status")
        previous_status = (
            light_result.get("previous_status")
            if "previous_status" in light_result
            else light_result.get("previous")
        )

        # For a raw detect() result, only forward on an actual transition.
        if "transition" in light_result and not transition:
            return None

        new_status = new_status if new_status in _LIGHT_STATES else "UNKNOWN"
        previous_status = (
            previous_status if previous_status in _LIGHT_STATES else "UNKNOWN"
        )

        machine_id = self._resolve_machine_id(light_result, snapshot or {})
        msg = MachineEventMsg(
            event_id="",
            machine_id=machine_id,
            produced_at=self._clock(),
            previous_status=previous_status,
            new_status=new_status,
        )
        _assert_credential_free(msg.model_dump(mode="json"))
        event_id = self._sync.submit_machine_event(msg)
        logger.debug(
            "EventBridge forwarded machine light transition",
            machine_id=machine_id,
            previous_status=previous_status,
            new_status=new_status,
            event_id=event_id,
        )
        return event_id


def _annotate_and_encode(frame: Any, meta: Dict[str, Any], quality: int) -> bytes:
    """Annotate ``frame`` with alert context and JPEG-encode it to bytes."""
    annotated = frame.copy()
    height = annotated.shape[0]
    lines = [
        f"ALERT: {meta.get('alert_type', 'unknown')}",
        f"machine: {meta.get('machine_id', '?')}",
    ]
    message = meta.get("message")
    if message:
        lines.append(str(message))
    ts = meta.get("timestamp")
    if isinstance(ts, datetime):
        lines.append(ts.isoformat())

    y = 24
    for line in lines:
        cv2.putText(
            annotated,
            line,
            (10, min(y, max(20, height - 10))),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
            cv2.LINE_AA,
        )
        y += 24

    ok, buf = cv2.imencode(
        ".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)]
    )
    if not ok:
        raise ValueError("cv2.imencode failed to encode the Event_Image")
    return buf.tobytes()
