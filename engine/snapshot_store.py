"""Snapshot_Store — Cloud_Server most-recent Snapshot_Thumbnail cache.

The Edge_Agent pushes a reduced-resolution ``Snapshot_Thumbnail`` per active
machine (raw JPEG body to ``POST /api/ingest/snapshot``). The Cloud_Server keeps
only the *most recently received* snapshot per machine ID and makes it available
to the Dashboard live tile (Requirements 9.4, 10.1).

Design points:
- **Last-write-wins per machine ID** — each new snapshot for a machine replaces
  the previous one; only one image is retained per machine (Requirement 9.4).
- **In-memory** — snapshots are ephemeral live-view context, never persisted to
  the Database and never written to the Object_Store (those are only for alert
  Event_Images, Requirement 8.4).
- **Thread-safe** — guarded by a lock so concurrent ingest writes and Dashboard
  reads (FastAPI runs handlers on a threadpool/loop) never tear a read.

Requirements: 9.4, 10.1
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_CONTENT_TYPE = "image/jpeg"


@dataclass(frozen=True)
class SnapshotEntry:
    """A single most-recent Snapshot_Thumbnail for one machine."""

    machine_id: str
    data: bytes
    content_type: str
    received_at: float  # epoch seconds


class SnapshotStore:
    """In-memory, last-write-wins most-recent snapshot per machine ID (Req 9.4)."""

    def __init__(self):
        self._snapshots: Dict[str, SnapshotEntry] = {}
        self._lock = threading.Lock()

    def put(
        self,
        machine_id: str,
        jpeg_bytes: bytes,
        content_type: str = _DEFAULT_CONTENT_TYPE,
    ) -> SnapshotEntry:
        """Store ``jpeg_bytes`` as the latest snapshot for ``machine_id``.

        Last-write-wins: replaces any prior snapshot for the machine so exactly
        one image is retained per machine ID (Requirement 9.4).
        """
        if not machine_id:
            raise ValueError("machine_id must be a non-empty string")
        entry = SnapshotEntry(
            machine_id=machine_id,
            data=bytes(jpeg_bytes),
            content_type=content_type or _DEFAULT_CONTENT_TYPE,
            received_at=time.time(),
        )
        with self._lock:
            self._snapshots[machine_id] = entry
        logger.debug(
            "Snapshot_Store stored latest snapshot",
            machine_id=machine_id,
            bytes=len(entry.data),
        )
        return entry

    def get(self, machine_id: str) -> Optional[SnapshotEntry]:
        """Return the most-recent snapshot for ``machine_id`` or ``None``."""
        with self._lock:
            return self._snapshots.get(machine_id)

    def has(self, machine_id: str) -> bool:
        """Return whether a snapshot exists for ``machine_id``."""
        with self._lock:
            return machine_id in self._snapshots

    def clear(self) -> None:
        """Drop all stored snapshots (used by tests)."""
        with self._lock:
            self._snapshots.clear()


# ── Module singleton ──────────────────────────────────────────────
# The Ingest_API (writer) and the staff Dashboard route (reader) must share the
# same store instance without server.py wiring, so both resolve it lazily here.
_store: Optional[SnapshotStore] = None
_store_lock = threading.Lock()


def get_snapshot_store() -> SnapshotStore:
    """Return the process-wide shared :class:`SnapshotStore` singleton."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SnapshotStore()
    return _store
