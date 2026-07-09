"""Property-based tests for the Edge_Agent Offline_Queue.

These tests validate the durable outbound buffer defined in
``edge/offline_queue.py`` (``OfflineQueue`` + ``OutboundEvent``) against the
Correctness Properties in the edge-cloud-split design document.

Durability/restart properties (Property 11) run against a REAL temporary
on-disk SQLite file provided by pytest's ``tmp_path`` fixture, so the
close/reopen behavior exercises actual disk persistence.

Feature: edge-cloud-split
"""

import os
import sys
import tempfile
from contextlib import contextmanager

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from edge.offline_queue import (  # noqa: E402
    DURABLE_KINDS,
    OfflineQueue,
    OfflineQueueError,
    OutboundEvent,
)

# ── Shared strategies ─────────────────────────────────────

DURABLE_KIND_ST = st.sampled_from(sorted(DURABLE_KINDS))
NON_DURABLE_KIND_ST = st.sampled_from(["heartbeat", "snapshot"])

# ISO-8601-ish timestamps drawn from a bounded range so ordering is meaningful.
PRODUCED_AT_ST = st.integers(min_value=0, max_value=10_000).map(
    lambda s: f"2026-01-01T00:00:{s % 60:02d}.{(s * 137) % 1000:03d}Z_{s:05d}"
)

MACHINE_ID_ST = st.sampled_from(["M-01", "M-02", "M-03", "M-99"])

PAYLOAD_ST = st.dictionaries(
    keys=st.text(min_size=1, max_size=8),
    values=st.one_of(
        st.integers(),
        st.text(max_size=16),
        st.booleans(),
        st.none(),
    ),
    max_size=4,
)


def _durable_event(event_id, machine_id="M-01", kind="session",
                   produced_at="2026-01-01T00:00:00.000Z", payload=None):
    return OutboundEvent(
        event_id=event_id,
        machine_id=machine_id,
        kind=kind,
        produced_at=produced_at,
        payload=payload or {},
    )


@st.composite
def durable_events(draw, unique_ids=True, count=None):
    """Generate a list of durable OutboundEvents with unique event_ids."""
    n = count if count is not None else draw(st.integers(min_value=0, max_value=25))
    ids = draw(
        st.lists(
            st.uuids().map(str),
            min_size=n,
            max_size=n,
            unique=unique_ids,
        )
    )
    events = []
    for eid in ids:
        events.append(
            OutboundEvent(
                event_id=eid,
                machine_id=draw(MACHINE_ID_ST),
                kind=draw(DURABLE_KIND_ST),
                produced_at=draw(PRODUCED_AT_ST),
                payload=draw(PAYLOAD_ST),
            )
        )
    return events


@contextmanager
def temp_queue(max_events=None, name="queue.db"):
    """Yield an OfflineQueue backed by a REAL temporary on-disk SQLite file.

    A fresh temporary directory is created per invocation so each Hypothesis
    example gets an isolated on-disk store (and function-scoped pytest fixtures
    are avoided, which Hypothesis does not reset between generated inputs).
    """
    tmp_dir = tempfile.mkdtemp(prefix="offline_queue_pbt_")
    db_path = os.path.join(tmp_dir, name)
    queue = OfflineQueue(db_path) if max_events is None else OfflineQueue(db_path, max_events=max_events)
    try:
        yield queue, tmp_dir
    finally:
        try:
            queue.close()
        except Exception:
            pass


def _new_temp_dir():
    return tempfile.mkdtemp(prefix="offline_queue_pbt_")


# ── Property 10 ───────────────────────────────────────────
# Feature: edge-cloud-split, Property 10: Durable events are queued while
# unreachable — for any sequence of durable events produced while the
# Cloud_Server is unreachable, every such event is present in the Offline_Queue
# and the queue size equals the number of durable events produced.
class TestProperty10DurableEventsQueuedWhileUnreachable:
    @settings(max_examples=150, deadline=None)
    @given(events=durable_events())
    def test_all_durable_events_persist_while_unreachable(self, events):
        """Validates: Requirements 4.1"""
        with temp_queue() as (queue, _):
            # "Unreachable": we only enqueue, never confirm (no cloud ack).
            for ev in events:
                queue.enqueue(ev)

            # The queue size equals the number of (unique) durable events.
            assert queue.size() == len(events)

            # Every produced event is retained in the queue.
            # Drain via peek/confirm to inspect membership.
            queued_ids = _collect_all_ids(queue)
            produced_ids = {e.event_id for e in events}
            assert queued_ids == produced_ids

    @settings(max_examples=100, deadline=None)
    @given(
        kinds=st.lists(DURABLE_KIND_ST, min_size=1, max_size=20),
    )
    def test_session_alert_machine_event_all_durable(self, kinds):
        """Validates: Requirements 4.1 — session/alert/machine_event persist."""
        with temp_queue() as (queue, _):
            for i, kind in enumerate(kinds):
                queue.enqueue(
                    _durable_event(
                        event_id=f"evt-{i}",
                        kind=kind,
                        produced_at=f"2026-01-01T00:00:{i % 60:02d}.000Z",
                    )
                )
            assert queue.size() == len(kinds)


def _collect_all_ids(queue):
    """Return the set of all event_ids by peeking then confirming each head.

    Mutates the queue (drains it), so callers must not rely on contents after.
    """
    ids = set()
    while True:
        head = queue.peek_oldest()
        if head is None:
            break
        ids.add(head.event_id)
        queue.confirm(head.event_id)
    return ids


# ── Property 11 ───────────────────────────────────────────
# Feature: edge-cloud-split, Property 11: Queue durability survives restart —
# for any set of enqueued unconfirmed events, reopening the Offline_Queue store
# (simulating an Edge_Agent process restart) yields exactly the same set of
# events with preserved ordering.
class TestProperty11DurabilitySurvivesRestart:
    @settings(max_examples=150, deadline=None)
    @given(events=durable_events())
    def test_unconfirmed_events_survive_close_and_reopen(self, events):
        """Validates: Requirements 4.2

        Uses a REAL on-disk SQLite file: enqueue, close the queue (process
        restart), reopen the SAME file, and assert the unconfirmed events
        survive with identical membership and ordering.
        """
        tmp_dir = _new_temp_dir()
        db_path = os.path.join(tmp_dir, "restart_queue.db")

        # First "process": enqueue and record the flush order, then close.
        queue = OfflineQueue(db_path)
        try:
            for ev in events:
                queue.enqueue(ev)
            order_before = _peek_order(queue)
        finally:
            queue.close()

        # Second "process": reopen the same on-disk file.
        reopened = OfflineQueue(db_path)
        try:
            assert reopened.size() == len(events)
            order_after = _peek_order(reopened)

            # Same set of events survived.
            assert {e[0] for e in order_after} == {e.event_id for e in events}
            # Ordering (produced_at, seq) preserved across restart.
            assert order_after == order_before
        finally:
            reopened.close()


def _peek_order(queue):
    """Return the (event_id, produced_at, seq) list in flush order, non-destructively.

    Rebuilds the ordering by reading rows directly via repeated peek is not
    possible without draining, so we read through the private connection in a
    read-only way to preserve queue contents.
    """
    rows = queue._conn.execute(
        """
        SELECT event_id, produced_at, seq
        FROM outbound_events
        ORDER BY produced_at ASC, seq ASC
        """
    ).fetchall()
    return [(r["event_id"], r["produced_at"], r["seq"]) for r in rows]


# ── Property 12 ───────────────────────────────────────────
# Feature: edge-cloud-split, Property 12: Flush transmits in ascending
# production-time order — for any set of queued events with arbitrary
# production timestamps, the order in which the flusher transmits them is
# non-decreasing by production time (with insertion sequence as a stable
# tiebreaker).
class TestProperty12AscendingProductionTimeOrder:
    @settings(max_examples=150, deadline=None)
    @given(events=durable_events())
    def test_flush_order_is_non_decreasing_by_produced_at(self, events):
        """Validates: Requirements 4.3"""
        with temp_queue() as (queue, _):
            for ev in events:
                queue.enqueue(ev)

            # Drain the queue the way the flusher does: peek head, confirm, repeat.
            flushed = []
            while True:
                head = queue.peek_oldest()
                if head is None:
                    break
                flushed.append((head.produced_at, head.seq))
                queue.confirm(head.event_id)

            assert len(flushed) == len(events)
            # Non-decreasing by (produced_at, seq): seq is the stable tiebreaker.
            assert flushed == sorted(flushed, key=lambda x: (x[0], x[1]))


# ── Property 13 ───────────────────────────────────────────
# Feature: edge-cloud-split, Property 13: Enqueue/confirm round-trip removes
# exactly the confirmed event — for any queued event, confirming it removes it
# from the Offline_Queue and decreases the queue size by one; an event that is
# never confirmed remains in the queue.
class TestProperty13EnqueueConfirmRoundTrip:
    @settings(max_examples=150, deadline=None)
    @given(events=durable_events(), data=st.data())
    def test_confirm_removes_exactly_one_event(self, events, data):
        """Validates: Requirements 4.4, 5.4"""
        if not events:
            return  # nothing to confirm
        with temp_queue() as (queue, _):
            for ev in events:
                queue.enqueue(ev)

            size_before = queue.size()
            target = data.draw(st.sampled_from(events))

            queue.confirm(target.event_id)

            # Size decreased by exactly one.
            assert queue.size() == size_before - 1
            # The confirmed event is gone; all others remain.
            remaining = _collect_all_ids(queue)
            expected = {e.event_id for e in events} - {target.event_id}
            assert remaining == expected

    @settings(max_examples=100, deadline=None)
    @given(events=durable_events())
    def test_unconfirmed_events_remain(self, events):
        """Validates: Requirements 5.4 — never-confirmed events stay queued."""
        with temp_queue() as (queue, _):
            for ev in events:
                queue.enqueue(ev)
            # No confirm calls at all → everything stays.
            assert queue.size() == len(events)
            assert _collect_all_ids(queue) == {e.event_id for e in events}


# ── Property 15 ───────────────────────────────────────────
# Feature: edge-cloud-split, Property 15: Assigned Event_IDs are unique — for
# any batch of produced durable events, the Event_IDs assigned before queuing
# contain no duplicates. The queue enforces this via a UNIQUE constraint and
# idempotent enqueue; queued event_ids are therefore always distinct.
class TestProperty15UniqueEventIds:
    @settings(max_examples=150, deadline=None)
    @given(events=durable_events())
    def test_queued_event_ids_have_no_duplicates(self, events):
        """Validates: Requirements 4.6"""
        with temp_queue() as (queue, _):
            for ev in events:
                queue.enqueue(ev)
            all_ids = _peek_order(queue)
            id_list = [row[0] for row in all_ids]
            assert len(id_list) == len(set(id_list))

    @settings(max_examples=100, deadline=None)
    @given(
        base=durable_events(count=5),
    )
    def test_reenqueue_same_id_is_idempotent(self, base):
        """Validates: Requirements 4.6 — duplicate event_id never doubles."""
        with temp_queue() as (queue, _):
            for ev in base:
                queue.enqueue(ev)
            size_after_first = queue.size()
            # Enqueue the same events again — ids already present → no-ops.
            for ev in base:
                queue.enqueue(ev)
            assert queue.size() == size_after_first


# ── Property 16 ───────────────────────────────────────────
# Feature: edge-cloud-split, Property 16: Heartbeats and snapshots are never
# queued — for any interleaving of Heartbeats, Snapshot_Thumbnails, and durable
# events, the Offline_Queue contains only the durable events and never a
# Heartbeat or Snapshot_Thumbnail.
class TestProperty16HeartbeatsSnapshotsNeverQueued:
    @settings(max_examples=150, deadline=None)
    @given(kind=NON_DURABLE_KIND_ST)
    def test_enqueue_non_durable_raises(self, kind):
        """Validates: Requirements 4.7 — heartbeat/snapshot enqueue is rejected."""
        with temp_queue() as (queue, _):
            ev = OutboundEvent(
                event_id="hb-1",
                machine_id="M-01",
                kind=kind,
                produced_at="2026-01-01T00:00:00.000Z",
                payload={},
            )
            with pytest.raises(OfflineQueueError):
                queue.enqueue(ev)
            # Nothing was persisted.
            assert queue.size() == 0

    @settings(max_examples=150, deadline=None)
    @given(
        stream=st.lists(
            st.tuples(
                st.sampled_from(
                    sorted(DURABLE_KINDS) + ["heartbeat", "snapshot"]
                ),
                st.integers(min_value=0, max_value=9999),
            ),
            min_size=1,
            max_size=30,
        )
    )
    def test_interleaved_stream_keeps_only_durable(self, stream):
        """Validates: Requirements 4.7"""
        with temp_queue() as (queue, _):
            expected_durable = 0
            for i, (kind, ts) in enumerate(stream):
                ev = OutboundEvent(
                    event_id=f"evt-{i}",
                    machine_id="M-01",
                    kind=kind,
                    produced_at=f"2026-01-01T00:00:{ts % 60:02d}.{ts % 1000:03d}Z",
                    payload={},
                )
                if kind in DURABLE_KINDS:
                    queue.enqueue(ev)
                    expected_durable += 1
                else:
                    with pytest.raises(OfflineQueueError):
                        queue.enqueue(ev)

            assert queue.size() == expected_durable
            # No queued event is a heartbeat or snapshot.
            rows = queue._conn.execute(
                "SELECT DISTINCT kind FROM outbound_events"
            ).fetchall()
            kinds_present = {r["kind"] for r in rows}
            assert kinds_present <= DURABLE_KINDS
            assert "heartbeat" not in kinds_present
            assert "snapshot" not in kinds_present


# ── Property 17 ───────────────────────────────────────────
# Feature: edge-cloud-split, Property 17: Queue capacity bound with oldest-drop
# and drop accounting — for any sequence of enqueue operations, the queue size
# never exceeds max_events; whenever an enqueue occurs at capacity, the
# discarded event is the oldest queued event, the newest event is retained, and
# the recorded drop count increases by the number of dropped events.
class TestProperty17CapacityBoundOldestDrop:
    @settings(max_examples=150, deadline=None)
    @given(
        max_events=st.integers(min_value=1, max_value=8),
        n_events=st.integers(min_value=0, max_value=40),
    )
    def test_size_capped_and_oldest_dropped(self, max_events, n_events):
        """Validates: Requirements 4.8, 4.9"""
        with temp_queue(max_events=max_events) as (queue, _):
            inserted = []
            for i in range(n_events):
                # Strictly increasing produced_at so "oldest" == earliest inserted.
                ev = OutboundEvent(
                    event_id=f"evt-{i:04d}",
                    machine_id="M-01",
                    kind="session",
                    produced_at=f"2026-01-01T00:00:00.000Z_{i:05d}",
                    payload={"i": i},
                )
                queue.enqueue(ev)
                inserted.append(ev.event_id)

                # Invariant after every enqueue: size never exceeds capacity.
                assert queue.size() <= max_events

            expected_size = min(n_events, max_events)
            assert queue.size() == expected_size

            # Dropped count equals the number of events that overflowed.
            expected_dropped = max(0, n_events - max_events)
            assert queue.dropped_count() == expected_dropped

            # The retained events are exactly the newest `expected_size` events;
            # the oldest were the ones dropped.
            remaining = _peek_order(queue)
            remaining_ids = [row[0] for row in remaining]
            expected_ids = inserted[len(inserted) - expected_size:] if expected_size else []
            assert remaining_ids == expected_ids

            # The newest event is always retained (when any were inserted).
            if n_events > 0:
                assert inserted[-1] in remaining_ids

    @settings(max_examples=100, deadline=None)
    @given(max_events=st.integers(min_value=1, max_value=5))
    def test_drop_count_increments_by_one_per_overflow(self, max_events):
        """Validates: Requirements 4.9 — one drop recorded per overflow enqueue."""
        with temp_queue(max_events=max_events) as (queue, _):
            # Fill to capacity: no drops yet.
            for i in range(max_events):
                queue.enqueue(
                    _durable_event(
                        event_id=f"fill-{i}",
                        produced_at=f"2026-01-01T00:00:00.000Z_{i:05d}",
                    )
                )
            assert queue.dropped_count() == 0

            # Each further enqueue at capacity drops exactly one oldest event.
            for j in range(3):
                before = queue.dropped_count()
                queue.enqueue(
                    _durable_event(
                        event_id=f"over-{j}",
                        produced_at=f"2026-01-01T00:00:01.000Z_{j:05d}",
                    )
                )
                assert queue.size() == max_events
                assert queue.dropped_count() == before + 1
