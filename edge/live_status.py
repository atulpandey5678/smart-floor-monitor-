"""Edge live-status producer — Heartbeat + Snapshot_Thumbnail per frame.

The Edge_Agent feeds the Cloud_Server's Live_State_Cache and Dashboard live
tiles with two *best-effort* signals derived from each processed frame:

- **Heartbeat** — a small live-status message (state, worker-present, active
  duration, machine light, camera health). Sent within 500 ms of a
  Session_Manager state change and, while a session is active, every 2 s
  (±500 ms). Never queued (Requirements 6.1, 6.2, 6.3).
- **Snapshot_Thumbnail** — a reduced-resolution JPEG for the live-view tile,
  pushed at a 2–5 s cadence while active. Each dimension is ≤ the configured
  cap and ≤ the source dimension (Requirements 9.1, 9.2). No continuous or
  full-frame-rate video is ever sent (Requirement 9.3).

:class:`LiveStatusPublisher` separates *pure decision logic* (when a heartbeat
is due, how to classify camera health, how to downscale a frame) — which is
synchronous and unit-testable — from the *async transmission* through the
Sync_Client. The edge loop (task 15.1) calls :meth:`publish` once per frame
from its event loop; the scheduling methods can also be exercised directly.

Requirements: 6.1, 6.2, 6.3, 9.1, 9.2
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional, Tuple

import structlog

from api.ingest_schemas import Heartbeat

logger = structlog.get_logger(__name__)

# Optional OpenCV — used to downscale + JPEG-encode the Snapshot_Thumbnail.
# Guarded so this module stays importable without OpenCV (heartbeats still
# work; snapshots are skipped).
try:  # pragma: no cover - availability depends on the host
    import cv2  # type: ignore

    _CV2_AVAILABLE = True
except Exception:  # pragma: no cover - defensive
    cv2 = None  # type: ignore
    _CV2_AVAILABLE = False


# Session_Manager states in which a session is considered "active" for the
# purpose of the 2 s heartbeat cadence and snapshot pushes. These are the
# states in which a session is open (running, temporarily absent, or static).
SESSION_ACTIVE_STATES = frozenset({"ACTIVE", "GRACE", "ABANDONED"})

_LIGHT_STATES = frozenset({"GREEN", "AMBER", "RED", "OFF", "UNKNOWN"})


def classify_camera_health(
    last_frame_age_s: Optional[float],
    *,
    connected: bool = True,
    healthy_max_age_s: float = 2.0,
    degraded_max_age_s: float = 10.0,
) -> str:
    """Map the age of the most recent frame to a camera-health value.

    HEALTHY when a frame arrived within ``healthy_max_age_s`` (≤ 2 s by
    default), DEGRADED when ``healthy_max_age_s < age ≤ degraded_max_age_s``
    (2–10 s), FAILED when the age exceeds ``degraded_max_age_s`` (> 10 s) or the
    stream is disconnected / no frame has ever arrived (Requirement 6.3).
    """
    if not connected or last_frame_age_s is None:
        return "FAILED"
    if last_frame_age_s <= healthy_max_age_s:
        return "HEALTHY"
    if last_frame_age_s <= degraded_max_age_s:
        return "DEGRADED"
    return "FAILED"


class LiveStatusPublisher:
    """Produces and transmits Heartbeats and Snapshot_Thumbnails per frame.

    Parameters
    ----------
    sync_client:
        The :class:`~edge.sync_client.SyncClient`. Only the best-effort
        ``send_heartbeat`` / ``send_snapshot`` methods are used (never queued).
    machine_id:
        Fallback machine ID when the snapshot omits one.
    clock:
        Monotonic-seconds callable (injectable for tests). Defaults to
        :func:`time.monotonic`.
    heartbeat_interval_s / heartbeat_tolerance_s:
        Active-session heartbeat cadence and jitter (Requirement 6.2). A
        heartbeat is due once ``interval - tolerance`` seconds have elapsed.
    snapshot_interval_s:
        Snapshot cadence while active, clamped to 2–5 s (Requirement 9.1).
    thumbnail_max_dim:
        Maximum thumbnail width/height in pixels (Requirement 9.2).
    healthy_max_age_s / degraded_max_age_s:
        Camera-health thresholds (Requirement 6.3).
    jpeg_quality:
        JPEG quality (0-100) for the encoded thumbnail.
    """

    def __init__(
        self,
        sync_client,
        *,
        machine_id: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
        heartbeat_interval_s: float = 2.0,
        heartbeat_tolerance_s: float = 0.5,
        snapshot_interval_s: float = 3.0,
        thumbnail_max_dim: int = 320,
        healthy_max_age_s: float = 2.0,
        degraded_max_age_s: float = 10.0,
        jpeg_quality: int = 80,
    ) -> None:
        self._sync = sync_client
        self._machine_id = machine_id
        self._clock = clock
        self._heartbeat_interval_s = float(heartbeat_interval_s)
        self._heartbeat_tolerance_s = float(heartbeat_tolerance_s)
        # Clamp snapshot cadence to the 2–5 s range (Requirement 9.1).
        self._snapshot_interval_s = min(5.0, max(2.0, float(snapshot_interval_s)))
        self._thumbnail_max_dim = int(thumbnail_max_dim)
        self._healthy_max_age_s = float(healthy_max_age_s)
        self._degraded_max_age_s = float(degraded_max_age_s)
        self._jpeg_quality = int(jpeg_quality)

        self._last_sent_state: Optional[str] = None
        self._last_heartbeat_at: Optional[float] = None
        self._last_snapshot_at: Optional[float] = None

    # ── Heartbeat construction ────────────────────────────
    def build_heartbeat(
        self,
        snapshot: Dict[str, Any],
        *,
        last_frame_age_s: Optional[float] = None,
        connected: bool = True,
        machine_light: str = "UNKNOWN",
    ) -> Heartbeat:
        """Build a schema-valid :class:`Heartbeat` from a snapshot dict.

        The active duration is floored to whole non-negative seconds
        (Requirement 6.3); ``worker_present`` comes from body detection; the
        machine-light value is validated against its allowed set (falling back
        to ``UNKNOWN``); ``camera_health`` is derived from the frame age.
        """
        machine_id = snapshot.get("machine_id") or self._machine_id
        if not machine_id:
            raise ValueError(
                "LiveStatusPublisher could not resolve a machine_id; construct "
                "the SessionManager with a machine_id or pass one to the publisher"
            )

        duration = snapshot.get("active_duration_seconds", 0) or 0
        whole_seconds = max(0, int(duration))

        light = machine_light if machine_light in _LIGHT_STATES else "UNKNOWN"
        health = classify_camera_health(
            last_frame_age_s,
            connected=connected,
            healthy_max_age_s=self._healthy_max_age_s,
            degraded_max_age_s=self._degraded_max_age_s,
        )

        return Heartbeat(
            machine_id=machine_id,
            state=snapshot.get("state", "IDLE"),
            worker_present=bool(snapshot.get("body_detected", False)),
            active_duration_seconds=whole_seconds,
            machine_light=light,
            camera_health=health,
        )

    # ── Scheduling decisions (pure) ───────────────────────
    @staticmethod
    def is_active(state: Optional[str]) -> bool:
        """Whether ``state`` counts as an active (open) session."""
        return state in SESSION_ACTIVE_STATES

    def heartbeat_due(self, state: Optional[str], now: float) -> Tuple[bool, str]:
        """Decide whether a Heartbeat should be sent now.

        Returns ``(due, reason)`` where reason is ``"state_change"``,
        ``"interval"``, ``"initial"`` or ``""``. A state change always sends
        (reported on the next frame, well within the 500 ms bound —
        Requirement 6.1); while active, a heartbeat is due every
        ``interval - tolerance`` seconds (Requirement 6.2).
        """
        if state != self._last_sent_state:
            return True, "state_change"
        if self._last_heartbeat_at is None:
            return True, "initial"
        if self.is_active(state):
            elapsed = now - self._last_heartbeat_at
            if elapsed >= (self._heartbeat_interval_s - self._heartbeat_tolerance_s):
                return True, "interval"
        return False, ""

    def _note_heartbeat_sent(self, state: Optional[str], now: float) -> None:
        self._last_sent_state = state
        self._last_heartbeat_at = now

    def snapshot_due(self, state: Optional[str], now: float) -> bool:
        """Whether a Snapshot_Thumbnail should be pushed now (active only)."""
        if not self.is_active(state):
            return False
        if self._last_snapshot_at is None:
            return True
        return (now - self._last_snapshot_at) >= self._snapshot_interval_s

    def _note_snapshot_sent(self, now: float) -> None:
        self._last_snapshot_at = now

    # ── Thumbnail production ──────────────────────────────
    def make_thumbnail(self, frame: Any) -> Optional[bytes]:
        """Downscale ``frame`` to a reduced-resolution JPEG thumbnail.

        Each output dimension is ≤ the configured cap and ≤ the corresponding
        source dimension (Requirement 9.2). Returns ``None`` when OpenCV is
        unavailable or the frame is empty/invalid.
        """
        if frame is None or not _CV2_AVAILABLE:
            return None
        try:
            height, width = frame.shape[:2]
        except (AttributeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None

        # Scale so the longest side fits the cap; never upscale (≤ source dim).
        scale = min(self._thumbnail_max_dim / float(width),
                    self._thumbnail_max_dim / float(height),
                    1.0)
        new_w = max(1, int(width * scale))
        new_h = max(1, int(height * scale))
        # Guard against rounding pushing a dimension over the cap.
        new_w = min(new_w, self._thumbnail_max_dim, width)
        new_h = min(new_h, self._thumbnail_max_dim, height)

        try:
            resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(
                ".jpg", resized, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
            )
        except Exception as exc:  # noqa: BLE001 - snapshot is best-effort
            logger.warning("Snapshot_Thumbnail encode failed", error=str(exc))
            return None
        if not ok:
            return None
        return buf.tobytes()

    # ── Per-frame publish (async transmission) ────────────
    async def publish(
        self,
        snapshot: Dict[str, Any],
        frame: Any = None,
        *,
        last_frame_age_s: Optional[float] = None,
        connected: bool = True,
        machine_light: str = "UNKNOWN",
        now: Optional[float] = None,
    ) -> Dict[str, bool]:
        """Send a Heartbeat and/or Snapshot_Thumbnail if due for this frame.

        Best-effort: transmission failures are swallowed by the Sync_Client and
        reflected in the returned flags. Returns
        ``{"heartbeat_sent": bool, "snapshot_sent": bool}``.
        """
        now = self._clock() if now is None else now
        state = snapshot.get("state")
        result = {"heartbeat_sent": False, "snapshot_sent": False}

        due, _reason = self.heartbeat_due(state, now)
        if due:
            hb = self.build_heartbeat(
                snapshot,
                last_frame_age_s=last_frame_age_s,
                connected=connected,
                machine_light=machine_light,
            )
            result["heartbeat_sent"] = await self._sync.send_heartbeat(hb)
            # Mark as sent regardless of transport success: the cadence tracks
            # attempts so a lost heartbeat is superseded by the next tick.
            self._note_heartbeat_sent(state, now)

        if self.snapshot_due(state, now):
            jpeg = self.make_thumbnail(frame)
            if jpeg is not None:
                machine_id = snapshot.get("machine_id") or self._machine_id
                result["snapshot_sent"] = await self._sync.send_snapshot(
                    machine_id, jpeg
                )
            self._note_snapshot_sent(now)

        return result
