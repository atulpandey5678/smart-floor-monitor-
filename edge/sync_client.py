"""Sync_Client — the Edge_Agent's channel to the Cloud_Server Ingest_API.

The Sync_Client owns every outbound HTTP interaction with the Cloud_Server:

- **Durable events** (Session_Records, Alerts, Machine_Events) are assigned a
  unique ``Event_ID`` and appended to the :class:`~edge.offline_queue.OfflineQueue`
  so nothing is lost while the cloud is unreachable. The submit methods return
  immediately with the assigned ``event_id``; a background flusher (task 12.2)
  drains the queue in production-time order.
- **Best-effort messages** (Heartbeats, Snapshot_Thumbnails) are transmitted
  directly and *never* queued (Requirement 4.7). A failed heartbeat is simply
  dropped — the next tick supersedes it.
- **Metadata** is pulled from ``GET /api/ingest/machines`` on startup and polled
  periodically (Requirement 7.2, 7.3).

Security invariants enforced here:

- The configured base URL MUST use the ``https`` scheme; construction fails
  fast otherwise so event data and (indirectly) credentials are never sent in
  the clear (Requirement 13.5).
- The ``Ingest_API_Key`` is attached as a default header on the underlying HTTP
  client, so it accompanies *every* request the Sync_Client makes
  (Requirements 3.1, 3.2).

Reachability (Requirement 4.1): a transmission attempt "fails" when no HTTP
response is received within the transmission timeout (default 10 s) — i.e. a
transport/timeout error. The Cloud_Server is considered **unreachable** after a
configurable number of consecutive failed attempts (default 3), at which point
callers route new durable events straight to the Offline_Queue. Receiving *any*
HTTP response (even a 4xx) proves connectivity and resets the failure count.

This module implements the Sync_Client **core** only. The durable-event flusher
(task 12.2) and the metadata poller (task 12.3) are separate background loops;
they are intentionally not implemented here, but the surface needed to build
them is exposed: :attr:`SyncClient.queue`, :meth:`SyncClient.send_durable_event`
(send a single queued event and report confirmation), and the reachability
state (:attr:`SyncClient.is_reachable`, :attr:`SyncClient.consecutive_failures`).

Requirements: 3.1, 3.2, 7.2, 9.3, 13.4, 13.5
"""

from __future__ import annotations

import asyncio
import base64
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional
from urllib.parse import urlsplit

import httpx
import structlog

from api.ingest_auth import INGEST_KEY_HEADER
from api.ingest_schemas import (
    AlertMsg,
    Heartbeat,
    MachineEventMsg,
    MachineMetadata,
    SessionRecordMsg,
)
from edge.offline_queue import OfflineQueue, OutboundEvent

logger = structlog.get_logger(__name__)


# Map each durable event ``kind`` to its Ingest_API endpoint path. These are
# relative to the configured base URL (which is validated https).
_KIND_PATHS = {
    "session": "/api/ingest/session",
    "alert": "/api/ingest/alert",
    "machine_event": "/api/ingest/machine-event",
}

_STATUS_PATH = "/api/ingest/status"
_SNAPSHOT_PATH = "/api/ingest/snapshot"
_MACHINES_PATH = "/api/ingest/machines"


# HTTP statuses the Cloud_Server returns for a durable event that can never
# succeed on retry, so the flusher dead-letters them instead of blocking the
# queue (design → Failure handling): 422 (schema / unknown machine) and 413
# (oversize body). 401 is deliberately *not* here — it is retained and retried
# because the operator can fix a misconfigured key.
_NON_RETRYABLE_STATUSES = frozenset({413, 422})


class SyncClientError(Exception):
    """Raised when a Sync_Client operation cannot be completed."""


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome of a single durable-event delivery attempt.

    Extends the plain-``bool`` contract of :meth:`SyncClient.send_durable_event`
    with the HTTP status so the flusher (task 12.2) can distinguish confirmed
    (200 → confirm/remove), non-retryable (422/413 → dead-letter/advance), and
    retainable (401/other/transport failure → keep queued and retry) outcomes.

    ``status_code`` is ``None`` when no HTTP response was received (transport or
    timeout error), i.e. a failed attempt against Requirement 4.1 reachability.
    """

    confirmed: bool
    status_code: Optional[int]

    @property
    def responded(self) -> bool:
        """Whether *any* HTTP response was received (connectivity proven)."""
        return self.status_code is not None

    @property
    def non_retryable(self) -> bool:
        """Whether the event should be dead-lettered rather than retried."""
        return self.status_code in _NON_RETRYABLE_STATUSES


class SyncClient:
    """Edge_Agent client for the Cloud_Server Ingest_API.

    Parameters
    ----------
    base_url:
        Cloud_Server base URL. MUST use the ``https`` scheme.
    api_key:
        The ``Ingest_API_Key`` attached to every request.
    queue:
        The durable :class:`OfflineQueue` durable events are appended to.
    timeout_s:
        Per-attempt transmission timeout in seconds (Requirement 4.1).
    ack_timeout_s:
        Single-delivery confirmation deadline in seconds (Requirement 5.5).
        Retained on the instance for the flusher (task 12.2); the core does not
        use it directly.
    unreachable_threshold:
        Consecutive failed attempts after which the cloud is deemed unreachable.
    transport:
        Optional ``httpx`` transport (e.g. ``httpx.MockTransport``) for tests.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        queue: OfflineQueue,
        *,
        timeout_s: float = 10.0,
        ack_timeout_s: float = 30.0,
        unreachable_threshold: int = 3,
        flush_retry_max_s: float = 60.0,
        flush_idle_interval_s: float = 1.0,
        backoff_initial_s: float = 1.0,
        backoff_max_s: float = 60.0,
        on_metadata: Optional[
            Callable[[List[MachineMetadata]], None]
        ] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = self._validate_https_base_url(base_url)
        if not api_key:
            raise SyncClientError("api_key must be a non-empty Ingest_API_Key")
        if unreachable_threshold < 1:
            raise SyncClientError("unreachable_threshold must be >= 1")
        if backoff_initial_s <= 0 or backoff_max_s <= 0:
            raise SyncClientError("backoff intervals must be positive")
        if backoff_max_s < backoff_initial_s:
            raise SyncClientError("backoff_max_s must be >= backoff_initial_s")

        self._api_key = api_key
        self._queue = queue
        self._timeout_s = float(timeout_s)
        self._ack_timeout_s = float(ack_timeout_s)
        self._unreachable_threshold = int(unreachable_threshold)

        # Flusher / poller tuning (Requirements 4.5, 5.5, 12.3).
        self._flush_retry_max_s = min(60.0, float(flush_retry_max_s))
        self._flush_idle_interval_s = float(flush_idle_interval_s)
        self._backoff_initial_s = float(backoff_initial_s)
        self._backoff_max_s = float(backoff_max_s)

        # Background-loop lifecycle state. Stop events and the poll interval are
        # (re)created when each loop is started so they bind to the running loop.
        self._flusher_task: Optional[asyncio.Task] = None
        self._poller_task: Optional[asyncio.Task] = None
        self._flusher_stop = asyncio.Event()
        self._poller_stop = asyncio.Event()
        self._poll_interval_s = 60.0
        # Set to wake the idle/retrying flusher immediately (e.g. on reconnect).
        self._flush_wakeup = asyncio.Event()

        # Last-known Machine_Metadata + change callback (Requirements 7.9, 12.4).
        self._last_metadata: Optional[List[MachineMetadata]] = None
        self._on_metadata = on_metadata

        # Consecutive transport failures; reset to 0 whenever any HTTP response
        # is received. Drives the reachability signal (Requirement 4.1).
        self._consecutive_failures = 0

        # The Ingest_API_Key rides on every request as a default header
        # (Requirements 3.1, 3.2). base_url is https, so all relative requests
        # inherit the https scheme (Requirement 13.5).
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={INGEST_KEY_HEADER: api_key},
            timeout=self._timeout_s,
            transport=transport,
        )
        logger.info(
            "SyncClient initialized",
            base_url=self._base_url,
            timeout_s=self._timeout_s,
            unreachable_threshold=self._unreachable_threshold,
        )

    # ── Construction helpers ──────────────────────────────
    @staticmethod
    def _validate_https_base_url(base_url: str) -> str:
        """Return the normalized base URL, raising unless it is https.

        Enforces Requirement 13.5: the Edge_Agent transmits to the Cloud_Server
        only over HTTPS. A non-https (or schemeless) base URL is rejected at
        construction time so an insecure client can never be built.
        """
        if not base_url:
            raise SyncClientError("base_url must be a non-empty https:// URL")
        parts = urlsplit(base_url)
        if parts.scheme.lower() != "https":
            raise SyncClientError(
                f"base_url must use the https scheme, got {parts.scheme or 'no'} "
                f"scheme in {base_url!r} (Requirement 13.5)"
            )
        if not parts.netloc:
            raise SyncClientError(f"base_url is missing a host: {base_url!r}")
        return base_url.rstrip("/")

    @classmethod
    def from_config(cls, queue: OfflineQueue, **overrides) -> "SyncClient":
        """Build a Sync_Client from ``config`` (``.env``-backed) values.

        Reads ``CLOUD_SERVER_BASE_URL`` and ``INGEST_API_KEY`` plus the sync
        timeouts/thresholds from :mod:`config`. Any keyword override takes
        precedence (useful for tests).
        """
        import config

        params = {
            "base_url": config.CLOUD_SERVER_BASE_URL,
            "api_key": config.INGEST_API_KEY,
            "timeout_s": config.SYNC_TRANSMISSION_TIMEOUT_SECONDS,
            "ack_timeout_s": config.SYNC_ACK_TIMEOUT_SECONDS,
            "unreachable_threshold": config.SYNC_UNREACHABLE_FAILURE_THRESHOLD,
            "flush_retry_max_s": config.SYNC_FLUSH_RETRY_MAX_SECONDS,
            "flush_idle_interval_s": config.SYNC_FLUSH_IDLE_INTERVAL_SECONDS,
            "backoff_initial_s": config.SYNC_RECONNECT_BACKOFF_INITIAL_SECONDS,
            "backoff_max_s": config.SYNC_RECONNECT_BACKOFF_MAX_SECONDS,
        }
        params.update(overrides)
        return cls(queue=queue, **params)

    # ── Reachability state (consumed by flusher/poller) ───
    @property
    def queue(self) -> OfflineQueue:
        """The durable Offline_Queue backing this client."""
        return self._queue

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive failed transmission attempts."""
        return self._consecutive_failures

    @property
    def is_reachable(self) -> bool:
        """Whether the Cloud_Server is currently considered reachable.

        ``False`` once :attr:`consecutive_failures` reaches the configured
        unreachable threshold (Requirement 4.1).
        """
        return self._consecutive_failures < self._unreachable_threshold

    @property
    def ack_timeout_s(self) -> float:
        """Single-delivery confirmation deadline (for the flusher, task 12.2)."""
        return self._ack_timeout_s

    def _note_reached(self) -> None:
        """Record that an HTTP response was received (connectivity proven)."""
        if self._consecutive_failures:
            logger.info(
                "SyncClient reachable again",
                previous_failures=self._consecutive_failures,
            )
        self._consecutive_failures = 0

    def _note_failed(self) -> None:
        """Record a failed transmission attempt (no response received)."""
        self._consecutive_failures += 1
        if self._consecutive_failures == self._unreachable_threshold:
            logger.warning(
                "SyncClient marking Cloud_Server unreachable",
                consecutive_failures=self._consecutive_failures,
            )

    # ── Durable submit (assign Event_ID, enqueue, return id) ──
    def submit_session(self, record: SessionRecordMsg) -> str:
        """Assign an Event_ID to ``record``, enqueue it, and return the id."""
        return self._enqueue_message("session", record)

    def submit_machine_event(self, event: MachineEventMsg) -> str:
        """Assign an Event_ID to ``event``, enqueue it, and return the id."""
        return self._enqueue_message("machine_event", event)

    def submit_alert(self, alert: AlertMsg, image: Optional[bytes] = None) -> str:
        """Assign an Event_ID to ``alert``, enqueue it, and return the id.

        If raw ``image`` bytes are supplied, they are base64-encoded and inlined
        into the alert payload (design: image inlined for alerts); otherwise the
        ``event_image_b64`` already on ``alert`` is used as-is.
        """
        if image is not None:
            encoded = base64.b64encode(image).decode("ascii")
            alert = alert.model_copy(update={"event_image_b64": encoded})
        return self._enqueue_message("alert", alert)

    def _enqueue_message(self, kind: str, msg) -> str:
        """Assign a unique Event_ID, build an OutboundEvent, and enqueue it.

        Returns the assigned ``event_id`` (Requirement 4.6 — uniqueness comes
        from :func:`uuid.uuid4`).
        """
        event_id = str(uuid.uuid4())
        # Overwrite whatever event_id the caller passed with the freshly
        # assigned one, so the id we return is authoritative and unique.
        stamped = msg.model_copy(update={"event_id": event_id})
        payload = stamped.model_dump(mode="json")

        event = OutboundEvent(
            event_id=event_id,
            machine_id=stamped.machine_id,
            kind=kind,
            produced_at=payload["produced_at"],  # ISO-8601 string ordering key
            payload=payload,
        )
        self._queue.enqueue(event)
        logger.debug(
            "SyncClient enqueued durable event", kind=kind, event_id=event_id
        )
        return event_id

    # ── Single durable send (used by the flusher, task 12.2) ──
    async def send_durable_event(self, event: OutboundEvent) -> bool:
        """Transmit one queued durable event; return ``True`` on confirmation.

        Backward-compatible boolean wrapper around
        :meth:`send_durable_event_result`: ``True`` only when the Cloud_Server
        confirms persistence with HTTP 200. Prefer the result variant in the
        flusher, which needs the status code to distinguish dead-letter from
        retain outcomes.
        """
        result = await self.send_durable_event_result(event)
        return result.confirmed

    async def send_durable_event_result(self, event: OutboundEvent) -> DeliveryResult:
        """Transmit one queued durable event and report the detailed outcome.

        Returns a :class:`DeliveryResult` carrying the HTTP status (or ``None``
        on a transport/timeout failure). Receiving any HTTP response marks the
        server reachable (Requirement 4.1); a transport error marks a failed
        attempt. This method never confirms/removes, dead-letters, or retries —
        the flusher owns that policy (task 12.2).
        """
        path = _KIND_PATHS.get(event.kind)
        if path is None:
            raise SyncClientError(f"cannot send non-durable kind {event.kind!r}")

        try:
            response = await self._client.post(path, json=event.payload)
        except httpx.HTTPError as exc:
            self._note_failed()
            logger.warning(
                "SyncClient durable send failed (no response)",
                kind=event.kind,
                event_id=event.event_id,
                error=str(exc),
            )
            return DeliveryResult(confirmed=False, status_code=None)

        self._note_reached()
        confirmed = response.status_code == 200
        if not confirmed:
            logger.warning(
                "SyncClient durable send not confirmed",
                kind=event.kind,
                event_id=event.event_id,
                status_code=response.status_code,
            )
        return DeliveryResult(confirmed=confirmed, status_code=response.status_code)

    # ── Best-effort messages (never queued) ───────────────
    async def send_heartbeat(self, hb: Heartbeat) -> bool:
        """Send a Heartbeat best-effort; never queued (Requirement 4.7).

        Returns ``True`` if the Cloud_Server accepted it (HTTP 200). Failures
        are swallowed (logged) — the next heartbeat supersedes a lost one.
        """
        try:
            response = await self._client.post(
                _STATUS_PATH, json=hb.model_dump(mode="json")
            )
        except httpx.HTTPError as exc:
            self._note_failed()
            logger.debug("Heartbeat send failed", error=str(exc))
            return False
        self._note_reached()
        return response.status_code == 200

    async def send_snapshot(self, machine_id: str, jpeg: bytes) -> bool:
        """Push a Snapshot_Thumbnail best-effort; never queued.

        The reduced-resolution JPEG is sent as the raw request body with the
        machine ID carried in a header. Best-effort: failures are logged and
        dropped (Requirements 9.3 — no continuous/full-rate video is sent).
        """
        try:
            response = await self._client.post(
                _SNAPSHOT_PATH,
                content=jpeg,
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Machine-Id": machine_id,
                },
            )
        except httpx.HTTPError as exc:
            self._note_failed()
            logger.debug("Snapshot send failed", machine_id=machine_id, error=str(exc))
            return False
        self._note_reached()
        return response.status_code == 200

    # ── Metadata pull (startup + poller) ──────────────────
    async def pull_metadata(self) -> List[MachineMetadata]:
        """Pull the current Machine_Metadata from the Cloud_Server.

        Returns the parsed, credential-free metadata list. Raises
        :class:`SyncClientError` on transport failure or a non-200 response so
        the caller (startup / poller) can retain last-known metadata
        (Requirement 7.9 — handled in task 12.3).
        """
        try:
            response = await self._client.get(_MACHINES_PATH)
        except httpx.HTTPError as exc:
            self._note_failed()
            raise SyncClientError(f"metadata pull failed: {exc}") from exc

        self._note_reached()
        if response.status_code != 200:
            raise SyncClientError(
                f"metadata pull returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SyncClientError(f"metadata response was not JSON: {exc}") from exc

        return [MachineMetadata.model_validate(item) for item in payload]

    # ── Durable-event flusher (task 12.2) ─────────────────
    #
    # A *single* background loop drains the Offline_Queue strictly in
    # production-time order, one unconfirmed event at a time (head-of-line —
    # Requirement 4.5 / 5.5). For each head event:
    #
    #   * HTTP 200 (confirmed)      → confirm()/remove and advance immediately.
    #   * HTTP 422/413 (poisoned)   → dead_letter()/remove and advance, so a
    #                                 permanently-rejected event never blocks
    #                                 the queue indefinitely.
    #   * HTTP 401 / other / no
    #     response within ack       → RETAIN the head and retry it after a
    #     deadline (30 s)             bounded interval (≤ 60 s), never sending a
    #                                 later-produced event ahead of it.
    #
    # The loop wakes early when :attr:`_flush_wakeup` is set (e.g. the poller
    # observed a reconnect, or a fresh event was submitted) so recovery is
    # prompt rather than waiting out the full retry interval.
    def start_flusher(self) -> asyncio.Task:
        """Start the background durable-event flusher (idempotent).

        Returns the running :class:`asyncio.Task`. Calling it again while the
        flusher is already running returns the existing task.
        """
        if self._flusher_task is not None and not self._flusher_task.done():
            return self._flusher_task
        self._flusher_stop = asyncio.Event()
        self._flush_wakeup = asyncio.Event()
        self._flusher_task = asyncio.create_task(
            self._run_flusher(), name="sync-client-flusher"
        )
        logger.info("SyncClient flusher started")
        return self._flusher_task

    async def stop_flusher(self) -> None:
        """Signal the flusher to stop and await its termination."""
        task = self._flusher_task
        if task is None:
            return
        self._flusher_stop.set()
        self._flush_wakeup.set()  # wake it out of any wait
        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover - defensive
            pass
        finally:
            self._flusher_task = None
        logger.info("SyncClient flusher stopped")

    def wake_flusher(self) -> None:
        """Nudge the flusher to retry the head immediately (e.g. on reconnect)."""
        self._flush_wakeup.set()

    async def _run_flusher(self) -> None:
        while not self._flusher_stop.is_set():
            head = self._queue.peek_oldest()
            if head is None:
                # Queue empty — idle-poll for freshly submitted work.
                await self._flusher_wait(self._flush_idle_interval_s)
                continue

            result = await self._deliver_with_ack_timeout(head)

            if result.confirmed:
                # Persisted (HTTP 200) — remove and advance to the next head
                # without delay (Requirement 4.4 / 5.4).
                self._queue.confirm(head.event_id)
                continue

            if result.non_retryable:
                # HTTP 422/413 can never succeed on retry — dead-letter it so
                # the head advances and good events are not blocked.
                self._queue.dead_letter(
                    head.event_id, reason=f"HTTP {result.status_code}"
                )
                logger.warning(
                    "SyncClient flusher dead-lettered poisoned head",
                    event_id=head.event_id,
                    status_code=result.status_code,
                )
                continue

            # Retainable failure (401 / other status / no response). Keep the
            # head queued and retry it after a bounded interval (≤ 60 s),
            # transmitting nothing later-produced in the meantime
            # (Requirement 4.5).
            logger.debug(
                "SyncClient flusher retaining head for retry",
                event_id=head.event_id,
                status_code=result.status_code,
            )
            await self._flusher_wait(self._flush_retry_max_s)

    async def _deliver_with_ack_timeout(self, event: OutboundEvent) -> DeliveryResult:
        """Send one event, treating no confirmation within ``ack_timeout_s`` as
        a failed attempt (Requirement 5.5).

        The underlying request already carries a per-attempt transmission
        timeout; the ack deadline is the outer bound that decides retry.
        """
        try:
            return await asyncio.wait_for(
                self.send_durable_event_result(event), timeout=self._ack_timeout_s
            )
        except asyncio.TimeoutError:
            self._note_failed()
            logger.warning(
                "SyncClient delivery exceeded ack deadline",
                event_id=event.event_id,
                ack_timeout_s=self._ack_timeout_s,
            )
            return DeliveryResult(confirmed=False, status_code=None)

    async def _flusher_wait(self, seconds: float) -> None:
        """Wait up to ``seconds``, waking early on stop or a flush wakeup."""
        stop_wait = asyncio.ensure_future(self._flusher_stop.wait())
        wake_wait = asyncio.ensure_future(self._flush_wakeup.wait())
        try:
            done, _ = await asyncio.wait(
                {stop_wait, wake_wait},
                timeout=seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_wait.cancel()
            wake_wait.cancel()
        if wake_wait in done:
            # Consume the wakeup so the next wait blocks again.
            self._flush_wakeup.clear()

    # ── Metadata poller (task 12.3) ───────────────────────
    #
    # Polls ``GET /api/ingest/machines`` at a configurable interval. On a
    # successful poll the last-known metadata is retained and, if it changed,
    # the on-change callback fires (consumed by the bootstrap/metadata-apply).
    # On a failing poll the last-known metadata is kept and the loop reconnects
    # with exponential backoff ``min(initial × 2^(n−1), max)`` (Requirement
    # 12.3). The first successful poll after a failure run resumes normal-cadence
    # polling *and* wakes the flusher to drain the queue (Requirement 12.4).
    def start_metadata_poller(
        self,
        on_change: Optional[Callable[[List[MachineMetadata]], None]] = None,
        interval_s: Optional[float] = None,
    ) -> asyncio.Task:
        """Start the background metadata poller (idempotent).

        Parameters
        ----------
        on_change:
            Callback invoked with the new metadata list whenever the polled
            metadata differs from the last-known value (including the first
            successful poll). Overrides any callback passed to the constructor.
        interval_s:
            Steady-state poll interval in seconds. Defaults to
            ``config.METADATA_POLL_INTERVAL_SECONDS`` (60 s, clamped 10–600).
        """
        if on_change is not None:
            self._on_metadata = on_change
        if interval_s is None:
            import config

            interval_s = config.METADATA_POLL_INTERVAL_SECONDS
        self._poll_interval_s = float(interval_s)

        if self._poller_task is not None and not self._poller_task.done():
            return self._poller_task
        self._poller_stop = asyncio.Event()
        self._poller_task = asyncio.create_task(
            self._run_metadata_poller(), name="sync-client-metadata-poller"
        )
        logger.info(
            "SyncClient metadata poller started", interval_s=self._poll_interval_s
        )
        return self._poller_task

    async def stop_metadata_poller(self) -> None:
        """Signal the metadata poller to stop and await its termination."""
        task = self._poller_task
        if task is None:
            return
        self._poller_stop.set()
        try:
            await task
        except asyncio.CancelledError:  # pragma: no cover - defensive
            pass
        finally:
            self._poller_task = None
        logger.info("SyncClient metadata poller stopped")

    @property
    def last_known_metadata(self) -> Optional[List[MachineMetadata]]:
        """The most recently pulled Machine_Metadata, retained across failures.

        ``None`` until the first successful pull/poll. On a failing poll this
        value is left unchanged so the Edge_Agent keeps operating on the
        last-known metadata (Requirement 7.9).
        """
        return self._last_metadata

    async def _run_metadata_poller(self) -> None:
        # Count of consecutive failed polls; drives exponential backoff.
        failures = 0
        while not self._poller_stop.is_set():
            try:
                metadata = await self.pull_metadata()
            except SyncClientError as exc:
                # Retain last-known metadata (Requirement 7.9) and reconnect
                # with exponential, capped backoff (Requirement 12.3).
                failures += 1
                delay = min(
                    self._backoff_initial_s * (2 ** (failures - 1)),
                    self._backoff_max_s,
                )
                logger.warning(
                    "SyncClient metadata poll failed; backing off",
                    consecutive_failures=failures,
                    backoff_s=delay,
                    error=str(exc),
                )
                await self._poller_wait(delay)
                continue

            if failures:
                # Connectivity restored: resume normal polling and flush the
                # Offline_Queue (Requirement 12.4).
                logger.info(
                    "SyncClient metadata poll recovered", previous_failures=failures
                )
                failures = 0
                self.wake_flusher()

            self._apply_metadata(metadata)
            await self._poller_wait(self._poll_interval_s)

    def _apply_metadata(self, metadata: List[MachineMetadata]) -> None:
        """Retain metadata and fire the on-change callback when it changed."""
        changed = metadata != self._last_metadata
        self._last_metadata = metadata
        if changed and self._on_metadata is not None:
            try:
                self._on_metadata(metadata)
            except Exception:  # pragma: no cover - callback is caller-owned
                logger.exception("SyncClient on_metadata callback raised")

    async def _poller_wait(self, seconds: float) -> None:
        """Wait up to ``seconds``, waking early only on poller stop."""
        try:
            await asyncio.wait_for(self._poller_stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ── Lifecycle ─────────────────────────────────────────
    async def aclose(self) -> None:
        """Stop background loops and close the underlying HTTP client."""
        await self.stop_flusher()
        await self.stop_metadata_poller()
        await self._client.aclose()

    async def __aenter__(self) -> "SyncClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.aclose()
