"""Cloud_Server in-memory Live_State_Cache and staleness sweeper.

The Live_State_Cache holds the most recent live status per machine, populated
by ingested Heartbeats (``POST /api/ingest/status``) and consumed by the
Dashboard WebSocket. It is deliberately I/O-free: an in-memory
``machine_id -> LiveState`` dictionary guarded by an ``asyncio.Lock``.

Responsibilities (Requirement 6):
- Update a machine's entry with the received Heartbeat values and a received
  timestamp — but only for a *valid* Heartbeat (6.4).
- Leave an existing entry untouched when a Heartbeat is invalid (6.5).
- Derive a ``liveness`` classification per machine:
    * ``UNKNOWN`` — no Heartbeat has ever been received (no entry) (6.8).
    * ``STALE``   — an entry exists but its most recent valid Heartbeat is
                    older than the configurable staleness interval (6.7).
    * ``LIVE``    — an entry exists and its Heartbeat is within the interval.
  ``UNKNOWN`` and ``STALE`` are always distinct (6.8).
- A background sweeper flips entries to ``STALE`` once they age past the
  staleness interval and notifies an optional broadcast callback so the
  WebSocket layer (task 8) can push the change to subscribed clients.

Requirements: 6.4, 6.5, 6.7, 6.8
"""

from __future__ import annotations

import asyncio
import time as time_module
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Dict, List, Optional, Union

import structlog
from pydantic import ValidationError

import config
from api.ingest_schemas import Heartbeat

logger = structlog.get_logger(__name__)


# ── Liveness vocabulary ───────────────────────────────────────────

LIVENESS_LIVE = "LIVE"
LIVENESS_STALE = "STALE"
LIVENESS_UNKNOWN = "UNKNOWN"


@dataclass
class LiveState:
    """The cached live status for a single machine.

    ``received_at`` is expressed in seconds using the cache's injected clock
    (epoch seconds by default). ``liveness`` is the last classification stored
    for the entry; note that reads always recompute liveness against the
    current time, so this field reflects the value as of the last update or
    sweep and is primarily used to detect transitions for broadcasting.
    """

    machine_id: str
    state: str
    worker_present: bool
    active_duration_seconds: int
    machine_light: str
    camera_health: str
    received_at: float
    liveness: str = LIVENESS_LIVE


# Broadcast callback: invoked with a LiveState when the sweeper changes an
# entry's liveness. May be sync or async; both are supported.
BroadcastCallback = Callable[[LiveState], Union[None, Awaitable[None]]]


class LiveStateCache:
    """In-memory per-machine live-status store guarded by an ``asyncio.Lock``.

    All public accessors acquire the lock, so the cache is safe to share across
    the ingest endpoint, the WebSocket layer, and the background sweeper.
    """

    def __init__(
        self,
        staleness_seconds: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
        broadcast: Optional[BroadcastCallback] = None,
    ) -> None:
        """Create a Live_State_Cache.

        Args:
            staleness_seconds: Interval after which an entry with no fresh
                valid Heartbeat is considered STALE. Defaults to
                ``config.LIVE_STATE_STALENESS_SECONDS`` and is clamped to the
                supported range (2–300 s, Requirement 6.7).
            clock: Injectable time source returning seconds. Defaults to
                ``time.time`` for production use.
            broadcast: Optional callback invoked by the sweeper whenever an
                entry's liveness changes (e.g. LIVE → STALE). Wired by the
                WebSocket layer in a later task.
        """
        self._clock = clock or time_module.time
        self._lock = asyncio.Lock()
        self._entries: Dict[str, LiveState] = {}
        self._broadcast = broadcast

        interval = (
            staleness_seconds
            if staleness_seconds is not None
            else config.LIVE_STATE_STALENESS_SECONDS
        )
        # Clamp to the supported range regardless of source (Requirement 6.7).
        self._staleness_seconds = self._clamp_staleness(interval)

        # Sweeper task handle (set by start_sweeper()).
        self._sweeper_task: Optional[asyncio.Task] = None
        self._sweep_stop: Optional[asyncio.Event] = None

    def set_broadcast_callback(self, broadcast: Optional[BroadcastCallback]) -> None:
        """Register (or replace) the broadcast callback after construction.

        The WebSocket layer wires this so that every cache mutation — a valid
        Heartbeat update as well as a sweeper liveness transition — is pushed to
        subscribed Dashboard clients (Requirement 6.6). Passing ``None`` clears
        the callback.
        """
        self._broadcast = broadcast

    @staticmethod
    def _clamp_staleness(value: float) -> float:
        return min(
            float(config.LIVE_STATE_STALENESS_MAX_SECONDS),
            max(float(config.LIVE_STATE_STALENESS_MIN_SECONDS), float(value)),
        )

    @property
    def staleness_seconds(self) -> float:
        """The effective (clamped) staleness interval in seconds."""
        return self._staleness_seconds

    # ── Liveness classification (pure) ────────────────────────────

    def _classify(self, entry: Optional[LiveState], now: float) -> str:
        """Classify liveness for an entry at time ``now``.

        No entry -> UNKNOWN; aged past the interval -> STALE; otherwise LIVE.
        """
        if entry is None:
            return LIVENESS_UNKNOWN
        age = now - entry.received_at
        if age > self._staleness_seconds:
            return LIVENESS_STALE
        return LIVENESS_LIVE

    # ── Updates ───────────────────────────────────────────────────

    async def update_from_heartbeat(self, heartbeat: Heartbeat) -> LiveState:
        """Update the entry for a *validated* Heartbeat (Requirement 6.4).

        Stores the received values plus a received timestamp and marks the
        entry LIVE. Returns the stored LiveState.
        """
        now = self._clock()
        state = LiveState(
            machine_id=heartbeat.machine_id,
            state=heartbeat.state,
            worker_present=heartbeat.worker_present,
            active_duration_seconds=heartbeat.active_duration_seconds,
            machine_light=heartbeat.machine_light,
            camera_health=heartbeat.camera_health,
            received_at=now,
            liveness=LIVENESS_LIVE,
        )
        async with self._lock:
            self._entries[heartbeat.machine_id] = state
        logger.debug(
            "Live_State_Cache updated",
            machine_id=heartbeat.machine_id,
            state=heartbeat.state,
            liveness=LIVENESS_LIVE,
        )
        # Broadcast the updated live status to subscribed Dashboard clients
        # (Requirement 6.6). Broadcasting on every valid Heartbeat keeps the
        # live tiles current; the sweeper additionally broadcasts STALE
        # transitions. No-op when no broadcast callback is wired.
        await self._emit(replace(state))
        return replace(state)

    async def apply_raw_heartbeat(
        self, payload: Union[Heartbeat, dict]
    ) -> tuple[bool, Optional[LiveState]]:
        """Validate and apply a Heartbeat payload.

        Accepts either a ready ``Heartbeat`` or a raw ``dict``. On a valid
        Heartbeat the entry is updated and ``(True, LiveState)`` is returned.
        On an invalid Heartbeat (missing field or value outside its defined
        set) the existing entry is left unchanged and ``(False, None)`` is
        returned so the caller can respond that the Heartbeat was invalid
        (Requirement 6.5).
        """
        if isinstance(payload, Heartbeat):
            heartbeat = payload
        else:
            try:
                heartbeat = Heartbeat(**payload)
            except (ValidationError, TypeError) as exc:
                logger.warning(
                    "Rejected invalid Heartbeat; cache entry left unchanged",
                    machine_id=(payload or {}).get("machine_id")
                    if isinstance(payload, dict)
                    else None,
                    error=str(exc),
                )
                return False, None
        state = await self.update_from_heartbeat(heartbeat)
        return True, state

    # ── Reads ─────────────────────────────────────────────────────

    async def get_liveness(self, machine_id: str) -> str:
        """Return the current liveness (LIVE/STALE/UNKNOWN) for a machine.

        Never-seen machines report UNKNOWN, distinct from STALE
        (Requirement 6.8).
        """
        now = self._clock()
        async with self._lock:
            return self._classify(self._entries.get(machine_id), now)

    async def get(self, machine_id: str) -> Optional[LiveState]:
        """Return a copy of the machine's LiveState with liveness recomputed.

        Returns ``None`` when no Heartbeat has ever been received for the
        machine (the caller should treat this as UNKNOWN, Requirement 6.8).
        """
        now = self._clock()
        async with self._lock:
            entry = self._entries.get(machine_id)
            if entry is None:
                return None
            return replace(entry, liveness=self._classify(entry, now))

    async def snapshot_all(self) -> List[LiveState]:
        """Return copies of all cached entries with liveness recomputed."""
        now = self._clock()
        async with self._lock:
            return [
                replace(entry, liveness=self._classify(entry, now))
                for entry in self._entries.values()
            ]

    # ── Staleness sweeper ─────────────────────────────────────────

    async def sweep_once(self) -> List[LiveState]:
        """Scan entries, flip newly-stale ones to STALE, and broadcast them.

        Returns the list of entries whose liveness changed during this sweep.
        """
        now = self._clock()
        changed: List[LiveState] = []
        async with self._lock:
            for machine_id, entry in self._entries.items():
                new_liveness = self._classify(entry, now)
                if new_liveness != entry.liveness:
                    updated = replace(entry, liveness=new_liveness)
                    self._entries[machine_id] = updated
                    changed.append(replace(updated))

        for entry in changed:
            logger.info(
                "Live_State_Cache liveness change",
                machine_id=entry.machine_id,
                liveness=entry.liveness,
            )
            await self._emit(entry)
        return changed

    async def _emit(self, entry: LiveState) -> None:
        """Invoke the broadcast callback, tolerating sync or async callbacks."""
        if self._broadcast is None:
            return
        try:
            result = self._broadcast(entry)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "Live_State_Cache broadcast callback failed",
                machine_id=entry.machine_id,
            )

    async def _sweep_loop(self, interval_seconds: float) -> None:
        assert self._sweep_stop is not None
        while not self._sweep_stop.is_set():
            try:
                await self.sweep_once()
            except Exception:  # pragma: no cover - defensive
                logger.exception("Live_State_Cache sweep iteration failed")
            try:
                await asyncio.wait_for(
                    self._sweep_stop.wait(), timeout=interval_seconds
                )
            except asyncio.TimeoutError:
                continue

    def start_sweeper(
        self, interval_seconds: Optional[float] = None
    ) -> asyncio.Task:
        """Start the background staleness sweeper.

        The sweeper periodically re-evaluates liveness and broadcasts entries
        that transition (e.g. LIVE → STALE). Idempotent: repeated calls return
        the already-running task.
        """
        if self._sweeper_task is not None and not self._sweeper_task.done():
            return self._sweeper_task
        interval = (
            interval_seconds
            if interval_seconds is not None
            else config.LIVE_STATE_SWEEP_INTERVAL_SECONDS
        )
        self._sweep_stop = asyncio.Event()
        self._sweeper_task = asyncio.create_task(self._sweep_loop(interval))
        logger.info(
            "Live_State_Cache sweeper started",
            interval_seconds=interval,
            staleness_seconds=self._staleness_seconds,
        )
        return self._sweeper_task

    async def stop_sweeper(self) -> None:
        """Stop the background sweeper and await its shutdown."""
        if self._sweep_stop is not None:
            self._sweep_stop.set()
        if self._sweeper_task is not None:
            try:
                await self._sweeper_task
            except asyncio.CancelledError:  # pragma: no cover - defensive
                pass
            self._sweeper_task = None
        logger.info("Live_State_Cache sweeper stopped")


# ── Module-level singleton ────────────────────────────────────────
# A single shared Live_State_Cache is used by the Ingest_API (which feeds it
# Heartbeats) and the WebSocket layer (which reads from it and broadcasts its
# updates). Both sides obtain the same instance via get_live_state_cache().

_cache_instance: Optional[LiveStateCache] = None


def get_live_state_cache() -> LiveStateCache:
    """Return the shared Live_State_Cache, creating it on first use."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = LiveStateCache()
    return _cache_instance


def set_live_state_cache(cache: Optional[LiveStateCache]) -> None:
    """Replace the shared Live_State_Cache instance (primarily for tests)."""
    global _cache_instance
    _cache_instance = cache
