"""Offline_Queue — the Edge_Agent's durable outbound buffer.

When the Cloud_Server is unreachable, the Edge_Agent persists each outbound
durable event (Session_Record, Alert, Machine_Event) to this queue and drains
it later, strictly in the order the events were produced. The queue is backed
by its **own** local SQLite file — separate from the Cloud_Server database —
so buffered events survive an Edge_Agent process restart with real on-disk
durability (Requirement 4.2).

Design points (see design.md → "Edge Offline_Queue schema"):

- Ordering key is the event **production time** (``produced_at``); the
  autoincrement ``seq`` is a stable tiebreaker for events sharing a timestamp
  (Requirement 4.3).
- ``event_id`` is ``UNIQUE`` — the same event enqueued twice is idempotent and
  never duplicated (Requirement 4.6 upstream assigns unique IDs).
- Capacity is capped at 100,000. On overflow the oldest event is discarded to
  make room for the new one, and a persisted ``dropped_count`` is incremented
  so the drop is observable (Requirements 4.8, 4.9).
- Only durable kinds (``session``, ``alert``, ``machine_event``) may be
  enqueued; Heartbeats and Snapshot_Thumbnails are rejected so they can never
  end up in the queue (Requirement 4.7).

The public methods (``enqueue``, ``peek_oldest``, ``confirm``, ``size``,
``dropped_count``) are synchronous and thread-safe.

Requirements: 4.2, 4.6, 4.7, 4.8, 4.9
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

# Maximum number of unconfirmed events retained in the queue (Requirement 4.8).
DEFAULT_MAX_EVENTS = 100_000

# The only event kinds that may be persisted to the Offline_Queue. Heartbeats
# and Snapshot_Thumbnails are deliberately excluded (Requirement 4.7).
DURABLE_KINDS = frozenset({"session", "alert", "machine_event"})

# queue_meta key under which the cumulative dropped-event count is stored.
_DROPPED_COUNT_KEY = "dropped_count"


class OfflineQueueError(Exception):
    """Raised when an Offline_Queue operation is invalid."""


@dataclass
class OutboundEvent:
    """A durable outbound event awaiting delivery to the Ingest_API.

    ``payload`` is the kind-specific JSON envelope body (for alerts the
    Event_Image is inlined as base64). ``seq`` is assigned by the queue on
    insert and is ``None`` for events that have not yet been enqueued.
    """

    event_id: str
    machine_id: str
    kind: str
    produced_at: str  # ISO-8601 string; ordering key
    payload: Dict[str, Any] = field(default_factory=dict)
    seq: Optional[int] = None


class OfflineQueue:
    """Durable, ordered, capacity-bounded outbound buffer on local SQLite."""

    def __init__(self, db_path: str, max_events: int = DEFAULT_MAX_EVENTS):
        if max_events <= 0:
            raise ValueError("max_events must be a positive integer")

        self._db_path = db_path
        self._max_events = int(max_events)
        self._lock = threading.Lock()

        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        # check_same_thread=False so the queue can be shared across the
        # Sync_Client's background loops; all access is serialized by _lock.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL keeps committed writes durable across a process restart while
        # allowing concurrent reads; NORMAL is safe for process-crash recovery.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()
        logger.info(
            "OfflineQueue opened", db_path=db_path, max_events=self._max_events
        )

    # ── Schema ────────────────────────────────────────────
    def _create_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_events (
                    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id     TEXT UNIQUE NOT NULL,
                    machine_id   TEXT NOT NULL,
                    kind         TEXT NOT NULL,
                    produced_at  TEXT NOT NULL,
                    payload      TEXT NOT NULL,
                    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_outbound_order
                    ON outbound_events(produced_at, seq)
                """
            )
            # Poisoned events that the Cloud_Server permanently rejects
            # (HTTP 422 schema/unknown-machine, 413 oversize) are moved here so
            # they can never block head-of-line delivery of good events, while
            # still being retained for operator inspection (design → Failure
            # handling; Requirements 4.5, 5.5).
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dead_letter_events (
                    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id        TEXT UNIQUE NOT NULL,
                    machine_id      TEXT NOT NULL,
                    kind            TEXT NOT NULL,
                    produced_at     TEXT NOT NULL,
                    payload         TEXT NOT NULL,
                    reason          TEXT,
                    dead_lettered_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO queue_meta(key, value) VALUES (?, ?)",
                (_DROPPED_COUNT_KEY, "0"),
            )

    # ── Public API ────────────────────────────────────────
    def enqueue(self, event: OutboundEvent) -> None:
        """Persist ``event`` to the queue.

        Rejects non-durable kinds (Heartbeat/Snapshot) so they can never be
        buffered (Requirement 4.7). When the queue is already at capacity, the
        oldest event(s) are discarded to make room and the dropped count is
        incremented (Requirements 4.8, 4.9). Re-enqueuing an event whose
        ``event_id`` is already present is a no-op (idempotent).
        """
        if event.kind not in DURABLE_KINDS:
            raise OfflineQueueError(
                f"refusing to enqueue non-durable kind {event.kind!r}; "
                f"only {sorted(DURABLE_KINDS)} may be queued"
            )
        if not event.event_id:
            raise OfflineQueueError("event_id must be a non-empty string")
        if not event.machine_id:
            raise OfflineQueueError("machine_id must be a non-empty string")

        payload_json = json.dumps(event.payload, separators=(",", ":"))

        with self._lock, self._conn:
            # Idempotent: an event_id already queued is left untouched.
            existing = self._conn.execute(
                "SELECT 1 FROM outbound_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                logger.debug(
                    "OfflineQueue enqueue ignored duplicate event_id",
                    event_id=event.event_id,
                )
                return

            # Enforce capacity: discard oldest until there is room for one more.
            dropped = 0
            while self._size_locked() >= self._max_events:
                oldest = self._conn.execute(
                    "SELECT seq FROM outbound_events "
                    "ORDER BY produced_at ASC, seq ASC LIMIT 1"
                ).fetchone()
                if oldest is None:
                    break
                self._conn.execute(
                    "DELETE FROM outbound_events WHERE seq = ?", (oldest["seq"],)
                )
                dropped += 1

            if dropped:
                self._increment_dropped_locked(dropped)
                logger.warning(
                    "OfflineQueue at capacity — dropped oldest events",
                    dropped=dropped,
                    max_events=self._max_events,
                )

            self._conn.execute(
                """
                INSERT INTO outbound_events (event_id, machine_id, kind, produced_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.machine_id,
                    event.kind,
                    event.produced_at,
                    payload_json,
                ),
            )

    def peek_oldest(self) -> Optional[OutboundEvent]:
        """Return the oldest queued event without removing it.

        "Oldest" is by ascending ``produced_at`` with ``seq`` as the stable
        tiebreaker (Requirement 4.3). Returns ``None`` when the queue is empty.
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT seq, event_id, machine_id, kind, produced_at, payload
                FROM outbound_events
                ORDER BY produced_at ASC, seq ASC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def confirm(self, event_id: str) -> None:
        """Remove the event with ``event_id`` after the Cloud_Server confirms it.

        A no-op if the event is not present (already confirmed/never queued).
        """
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM outbound_events WHERE event_id = ?", (event_id,)
            )

    def dead_letter(self, event_id: str, reason: str = "") -> bool:
        """Move a poisoned event out of the live queue into dead-letter storage.

        Used by the flusher when the Cloud_Server permanently rejects the head
        event (HTTP 422/413). The event is copied into ``dead_letter_events``
        and removed from ``outbound_events`` in a single transaction so the head
        advances and the queue is never blocked indefinitely (design → Failure
        handling). Returns ``True`` if an event was dead-lettered, ``False`` if
        no such event was queued. Idempotent: dead-lettering the same event_id
        twice leaves the recorded copy untouched.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT event_id, machine_id, kind, produced_at, payload
                FROM outbound_events WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            if row is None:
                return False
            self._conn.execute(
                """
                INSERT OR IGNORE INTO dead_letter_events
                    (event_id, machine_id, kind, produced_at, payload, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    row["event_id"],
                    row["machine_id"],
                    row["kind"],
                    row["produced_at"],
                    row["payload"],
                    reason,
                ),
            )
            self._conn.execute(
                "DELETE FROM outbound_events WHERE event_id = ?", (event_id,)
            )
        logger.warning(
            "OfflineQueue dead-lettered poisoned event",
            event_id=event_id,
            reason=reason,
        )
        return True

    def dead_letter_count(self) -> int:
        """Return the number of events currently held in dead-letter storage."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM dead_letter_events"
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    def size(self) -> int:
        """Return the number of events currently queued."""
        with self._lock:
            return self._size_locked()

    def dropped_count(self) -> int:
        """Return the cumulative number of events dropped due to capacity."""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM queue_meta WHERE key = ?",
                (_DROPPED_COUNT_KEY,),
            ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

    # ── Context manager ───────────────────────────────────
    def __enter__(self) -> "OfflineQueue":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ── Internal helpers (assume _lock held) ──────────────
    def _size_locked(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM outbound_events"
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def _increment_dropped_locked(self, delta: int) -> None:
        self._conn.execute(
            """
            INSERT INTO queue_meta(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE
                SET value = CAST(CAST(value AS INTEGER) + ? AS TEXT)
            """,
            (_DROPPED_COUNT_KEY, str(delta), delta),
        )

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> OutboundEvent:
        return OutboundEvent(
            event_id=row["event_id"],
            machine_id=row["machine_id"],
            kind=row["kind"],
            produced_at=row["produced_at"],
            payload=json.loads(row["payload"]),
            seq=row["seq"],
        )
