"""Property-based tests for the idempotent ingest repository methods.

Feature: edge-cloud-split

These tests exercise the cloud-side idempotent/upsert-aware ingest methods on
``db.repository.Repository`` (``ingest_session``, ``ingest_alert``,
``ingest_machine_event``, ``event_exists``) and the ``IngestResult`` outcome,
which back the Cloud_Server Ingest_API.

Each Hypothesis example runs against its own **temporary on-disk SQLite database**
with migrations 001-004 applied via ``db.migrations.MigrationRunner``. A single
migrated template database is built once per module and copied per example so
every example gets an isolated, real (durable) database rather than an in-memory
one — this matters for the atomicity property in particular.

Because Hypothesis does not drive ``async def`` test functions, each test is a
plain synchronous function that generates data with ``@given`` and runs the
async scenario against a freshly-copied database via ``asyncio.run``.
"""

import asyncio
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from api.ingest_schemas import (
    AlertMsg,
    Heartbeat,
    MachineEventMsg,
    SessionRecordMsg,
)
from db.async_database import AsyncDatabase
from db.migrations import MigrationRunner
from db.repository import INGEST_WORKER_BADGE_ID, IngestResult, Repository

# ── Locations ────────────────────────────────────────────────────

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

# Tables we snapshot for the atomicity property.
_SNAPSHOT_TABLES = [
    "employees",
    "sessions",
    "alerts",
    "machine_state_events",
    "ingested_events",
]


# ── Shared Hypothesis strategies ─────────────────────────────────

LIGHT_STATES = ["GREEN", "AMBER", "RED", "OFF", "UNKNOWN"]
SESSION_STATES = ["IDLE", "OPENING", "ACTIVE", "GRACE", "ABANDONED", "CLOSED"]
CAMERA_HEALTH = ["HEALTHY", "DEGRADED", "FAILED"]
SESSION_OPS = ["open", "update", "close"]

# Printable, control-char-free text so values round-trip cleanly through SQLite.
_printable = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=1,
    max_size=24,
)
_optional_printable = st.none() | _printable

# Machine IDs / identifiers: non-empty, no whitespace-only surprises.
_id_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=16,
)

_dt = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 1, 1),
)

_duration = st.floats(
    min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def session_msgs(draw, event_id=None, machine_id=None, session_uuid=None, op=None):
    """Build a valid SessionRecordMsg, allowing pinned fields for reuse."""
    _op = op if op is not None else draw(st.sampled_from(SESSION_OPS))
    return SessionRecordMsg(
        event_id=event_id if event_id is not None else draw(_id_text),
        machine_id=machine_id if machine_id is not None else draw(_id_text),
        session_uuid=session_uuid if session_uuid is not None else draw(_id_text),
        produced_at=draw(_dt),
        op=_op,
        start_time=draw(_dt),
        end_time=draw(st.none() | _dt),
        active_duration_seconds=draw(_duration),
        close_reason=draw(_optional_printable),
    )


@st.composite
def alert_msgs(draw, event_id=None, machine_id=None):
    return AlertMsg(
        event_id=event_id if event_id is not None else draw(_id_text),
        machine_id=machine_id if machine_id is not None else draw(_id_text),
        produced_at=draw(_dt),
        alert_type=draw(_printable),
        message=draw(_optional_printable),
        event_image_b64="",  # not persisted by the repository; endpoint handles it
    )


@st.composite
def machine_event_msgs(draw, event_id=None, machine_id=None):
    return MachineEventMsg(
        event_id=event_id if event_id is not None else draw(_id_text),
        machine_id=machine_id if machine_id is not None else draw(_id_text),
        produced_at=draw(_dt),
        previous_status=draw(st.sampled_from(LIGHT_STATES)),
        new_status=draw(st.sampled_from(LIGHT_STATES)),
    )


# ── Database plumbing ────────────────────────────────────────────


@pytest.fixture(scope="module")
def migrated_template(tmp_path_factory):
    """Build one migrated (001-004) SQLite template DB, shared for the module.

    Each example copies this file to get a fresh, isolated on-disk database
    without paying the migration cost every time.
    """
    template_dir = tmp_path_factory.mktemp("ingest_pbt_template")
    template_path = template_dir / "template.db"
    runner = MigrationRunner(str(template_path), MIGRATIONS_DIR)
    try:
        runner.run()
    finally:
        runner.close()
    yield str(template_path)


@contextmanager
def fresh_db_path(template_path):
    """Copy the migrated template to a brand-new temp DB file for one example."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    shutil.copyfile(template_path, path)
    try:
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


def run_scenario(template_path, scenario):
    """Run an async ``scenario(repo, db)`` against a fresh copied database."""

    async def _runner(path):
        db = AsyncDatabase(db_path=path)
        await db.connect()
        try:
            return await scenario(Repository(db), db)
        finally:
            await db.close()

    with fresh_db_path(template_path) as path:
        return asyncio.run(_runner(path))


async def _snapshot(db):
    """Return an ordered snapshot of every relevant table for equality checks."""
    snap = {}
    for table in _SNAPSHOT_TABLES:
        rows = await db.fetch_all(f"SELECT * FROM {table} ORDER BY rowid")
        snap[table] = [dict(r) for r in rows]
    return snap


_PBT = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# ══════════════════════════════════════════════════════════════════
# Property 2: Ingest persistence round-trip
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 2: For any valid Session_Record, Alert, or
# Machine_Event payload, after a successful push the record read back from the
# Database has field values equal to the pushed payload (for the fields the
# schema persists), and the response is HTTP 200 echoing the same Event_ID.
# Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.6
class TestProperty2RoundTrip:
    @given(msg=session_msgs())
    @_PBT
    def test_session_round_trip(self, migrated_template, msg):
        async def scenario(repo, db):
            result = await repo.ingest_session(msg)
            assert isinstance(result, IngestResult)
            # 200 echoing the same Event_ID:
            assert result.event_id == msg.event_id
            assert result.created is True

            row = await db.fetch_one(
                "SELECT * FROM sessions WHERE session_uuid = ?", (msg.session_uuid,)
            )
            assert row is not None
            is_close = msg.op == "close"
            assert row["machine_id"] == msg.machine_id
            assert row["session_uuid"] == msg.session_uuid
            assert row["event_id"] == msg.event_id
            assert row["start_time"] == msg.start_time.isoformat()
            assert row["active_duration_seconds"] == msg.active_duration_seconds
            assert row["state"] == ("CLOSED" if is_close else "ACTIVE")
            expected_end = msg.end_time.isoformat() if msg.end_time else None
            assert row["end_time"] == expected_end
            assert row["close_reason"] == (msg.close_reason if is_close else None)

        run_scenario(migrated_template, scenario)

    @given(msg=alert_msgs(), image_url=_printable)
    @_PBT
    def test_alert_round_trip(self, migrated_template, msg, image_url):
        async def scenario(repo, db):
            result = await repo.ingest_alert(msg, image_url)
            assert result.event_id == msg.event_id
            assert result.created is True

            row = await db.fetch_one(
                "SELECT * FROM alerts WHERE event_id = ?", (msg.event_id,)
            )
            assert row is not None
            assert row["machine_id"] == msg.machine_id
            assert row["alert_type"] == msg.alert_type
            assert row["message"] == msg.message
            assert row["event_id"] == msg.event_id
            assert row["event_image_url"] == image_url
            assert row["created_at"] == msg.produced_at.isoformat()

        run_scenario(migrated_template, scenario)

    @given(msg=machine_event_msgs())
    @_PBT
    def test_machine_event_round_trip(self, migrated_template, msg):
        async def scenario(repo, db):
            result = await repo.ingest_machine_event(msg)
            assert result.event_id == msg.event_id
            assert result.created is True

            row = await db.fetch_one(
                "SELECT * FROM machine_state_events WHERE event_id = ?",
                (msg.event_id,),
            )
            assert row is not None
            assert row["machine_id"] == msg.machine_id
            assert row["previous_status"] == msg.previous_status
            assert row["new_status"] == msg.new_status
            assert row["timestamp"] == msg.produced_at.isoformat()
            assert row["event_id"] == msg.event_id

        run_scenario(migrated_template, scenario)


# ══════════════════════════════════════════════════════════════════
# Property 6: Cloud idempotency by Event_ID
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 6: For any durable payload delivered one or
# more times (with the same Event_ID, even if later bodies differ), exactly one
# record exists afterward, that record equals the first successfully persisted
# version (unchanged by later duplicates), and every response is HTTP 200
# carrying that Event_ID.
# Validates: Requirements 2.10, 5.1, 5.2
class TestProperty6Idempotency:
    @given(
        data=st.data(),
        kind=st.sampled_from(["session", "alert", "machine_event"]),
        n_dupes=st.integers(min_value=1, max_value=4),
        machine_id=_id_text,
        event_id=_id_text,
    )
    @_PBT
    def test_duplicate_delivery_is_idempotent(
        self, migrated_template, data, kind, n_dupes, machine_id, event_id
    ):
        # Build a first message plus N duplicates that SHARE the Event_ID and
        # machine_id but otherwise differ ("later bodies differ").
        if kind == "session":
            first = data.draw(session_msgs(event_id=event_id, machine_id=machine_id))
            dupes = [
                data.draw(session_msgs(event_id=event_id, machine_id=machine_id))
                for _ in range(n_dupes)
            ]
            table = "sessions"
        elif kind == "alert":
            first = data.draw(alert_msgs(event_id=event_id, machine_id=machine_id))
            dupes = [
                data.draw(alert_msgs(event_id=event_id, machine_id=machine_id))
                for _ in range(n_dupes)
            ]
            table = "alerts"
        else:
            first = data.draw(
                machine_event_msgs(event_id=event_id, machine_id=machine_id)
            )
            dupes = [
                data.draw(machine_event_msgs(event_id=event_id, machine_id=machine_id))
                for _ in range(n_dupes)
            ]
            table = "machine_state_events"

        async def persist(repo, msg):
            if kind == "session":
                return await repo.ingest_session(msg)
            if kind == "alert":
                return await repo.ingest_alert(msg, "http://store/img.jpg")
            return await repo.ingest_machine_event(msg)

        async def scenario(repo, db):
            first_result = await persist(repo, first)
            assert first_result.event_id == event_id
            assert first_result.created is True

            # Capture the persisted "first version".
            first_row = await db.fetch_one(
                f"SELECT * FROM {table} WHERE event_id = ?", (event_id,)
            )
            assert first_row is not None
            first_snapshot = dict(first_row)

            # Deliver the duplicates: each must be an idempotent no-op (still 200).
            for dupe in dupes:
                res = await persist(repo, dupe)
                assert res.event_id == event_id
                assert res.created is False
                assert res.already_persisted is True

            # Exactly one domain record and one ledger row for the Event_ID.
            dom = await db.fetch_all(
                f"SELECT * FROM {table} WHERE event_id = ?", (event_id,)
            )
            assert len(dom) == 1
            ledger = await db.fetch_all(
                "SELECT * FROM ingested_events WHERE event_id = ?", (event_id,)
            )
            assert len(ledger) == 1

            # The record is unchanged by the later duplicates.
            assert dict(dom[0]) == first_snapshot
            assert await repo.event_exists(event_id) is True

        run_scenario(migrated_template, scenario)


# ══════════════════════════════════════════════════════════════════
# Property 7: Session upsert by session_uuid
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 7: For any sequence of Session_Record
# pushes (open, update, close) sharing one session_uuid but carrying distinct
# Event_IDs, exactly one Session_Record for that session_uuid exists afterward,
# reflecting the applied updates rather than duplicated rows.
# Validates: Requirements 5.3
class TestProperty7SessionUpsert:
    @given(
        data=st.data(),
        session_uuid=_id_text,
        machine_id=_id_text,
        ops=st.lists(st.sampled_from(SESSION_OPS), min_size=1, max_size=6),
    )
    @_PBT
    def test_upsert_collapses_to_single_row(
        self, migrated_template, data, session_uuid, machine_id, ops
    ):
        # Distinct Event_IDs (index-based) but a shared session_uuid.
        msgs = [
            data.draw(
                session_msgs(
                    event_id=f"evt-{i}",
                    machine_id=machine_id,
                    session_uuid=session_uuid,
                    op=op,
                )
            )
            for i, op in enumerate(ops)
        ]

        async def scenario(repo, db):
            for msg in msgs:
                res = await repo.ingest_session(msg)
                assert res.event_id == msg.event_id
                assert res.created is True  # distinct event_ids => each applies

            rows = await db.fetch_all(
                "SELECT * FROM sessions WHERE session_uuid = ?", (session_uuid,)
            )
            # Exactly one row for the session_uuid, no duplicated rows.
            assert len(rows) == 1
            row = dict(rows[0])

            # The single row reflects the LAST applied push (upsert overwrite).
            last = msgs[-1]
            last_is_close = last.op == "close"
            assert row["session_uuid"] == session_uuid
            assert row["machine_id"] == machine_id
            assert row["event_id"] == last.event_id
            assert row["start_time"] == last.start_time.isoformat()
            assert row["active_duration_seconds"] == last.active_duration_seconds
            assert row["state"] == ("CLOSED" if last_is_close else "ACTIVE")
            expected_end = last.end_time.isoformat() if last.end_time else None
            assert row["end_time"] == expected_end
            assert row["close_reason"] == (last.close_reason if last_is_close else None)

        run_scenario(migrated_template, scenario)


# ══════════════════════════════════════════════════════════════════
# Property 8: Persistence failure leaves the database unchanged
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 8: For any payload whose persistence is
# forced to fail, the Cloud_Server returns an error response and the complete
# database state after the attempt equals the state before it (no partial insert
# or modification).
# Validates: Requirements 5.6
class TestProperty8Atomicity:
    @given(
        data=st.data(),
        kind=st.sampled_from(["session", "alert", "machine_event"]),
    )
    @_PBT
    def test_forced_failure_is_atomic(self, migrated_template, data, kind):
        # A baseline record (persisted successfully) plus a target record whose
        # persistence we force to fail. The failing insert targets the domain
        # table AFTER the idempotency-ledger claim has already succeeded inside
        # the same transaction, so a non-atomic implementation would leak a
        # ledger row and/or a partial domain row.
        if kind == "session":
            baseline = data.draw(session_msgs())
            target = data.draw(session_msgs())
            fail_prefix = "INSERT INTO SESSIONS"
        elif kind == "alert":
            baseline = data.draw(alert_msgs())
            target = data.draw(alert_msgs())
            fail_prefix = "INSERT INTO ALERTS"
        else:
            baseline = data.draw(machine_event_msgs())
            target = data.draw(machine_event_msgs())
            fail_prefix = "INSERT INTO MACHINE_STATE_EVENTS"

        # Ensure the target has a different Event_ID than the baseline.
        if target.event_id == baseline.event_id:
            target = target.model_copy(update={"event_id": baseline.event_id + "-x"})

        async def persist(repo, msg):
            if kind == "session":
                return await repo.ingest_session(msg)
            if kind == "alert":
                return await repo.ingest_alert(msg, "http://store/img.jpg")
            return await repo.ingest_machine_event(msg)

        async def scenario(repo, db):
            # Persist the baseline so "unchanged" is a non-trivial state.
            await persist(repo, baseline)
            before = await _snapshot(db)

            # Inject a failing execute on the domain-table INSERT.
            conn = db._connection
            original_execute = conn.execute

            async def failing_execute(sql, parameters=()):
                if sql.strip().upper().startswith(fail_prefix):
                    raise RuntimeError("simulated persistence failure")
                return await original_execute(sql, parameters)

            conn.execute = failing_execute
            try:
                with pytest.raises(Exception):
                    await persist(repo, target)
            finally:
                conn.execute = original_execute

            after = await _snapshot(db)
            # Complete DB state is byte-for-byte identical: no partial writes.
            assert after == before
            # The failed Event_ID was never persisted (ledger rolled back too).
            assert await repo.event_exists(target.event_id) is False

        run_scenario(migrated_template, scenario)


# ══════════════════════════════════════════════════════════════════
# Property 9: Orphan close creates a closed record
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 9: For any Session_Record close push whose
# session_uuid has no matching existing open session, the Cloud_Server creates a
# Session_Record marked closed populated from the close push and responds with
# success.
# Validates: Requirements 5.7
class TestProperty9OrphanClose:
    @given(msg=session_msgs(op="close"))
    @_PBT
    def test_orphan_close_creates_closed_record(self, migrated_template, msg):
        async def scenario(repo, db):
            # No prior session exists for this session_uuid.
            existing = await db.fetch_one(
                "SELECT 1 FROM sessions WHERE session_uuid = ?", (msg.session_uuid,)
            )
            assert existing is None

            result = await repo.ingest_session(msg)
            assert result.event_id == msg.event_id
            assert result.created is True  # success response

            rows = await db.fetch_all(
                "SELECT * FROM sessions WHERE session_uuid = ?", (msg.session_uuid,)
            )
            assert len(rows) == 1
            row = dict(rows[0])
            # Created record is marked closed and populated from the close push.
            assert row["state"] == "CLOSED"
            assert row["machine_id"] == msg.machine_id
            assert row["session_uuid"] == msg.session_uuid
            assert row["event_id"] == msg.event_id
            assert row["close_reason"] == msg.close_reason
            assert row["start_time"] == msg.start_time.isoformat()
            expected_end = msg.end_time.isoformat() if msg.end_time else None
            assert row["end_time"] == expected_end

        run_scenario(migrated_template, scenario)


# ══════════════════════════════════════════════════════════════════
# Property 4: Every persisted record and heartbeat is machine-tagged
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 4: For any persisted Session_Record, Alert,
# or Machine_Event, and any produced Heartbeat, the machine ID field is present,
# non-empty, and equal to the originating machine ID of the payload.
# Validates: Requirements 2.7, 16.2
class TestProperty4MachineTagged:
    @given(
        session=session_msgs(),
        alert=alert_msgs(),
        machine_event=machine_event_msgs(),
    )
    @_PBT
    def test_persisted_records_are_machine_tagged(
        self, migrated_template, session, alert, machine_event
    ):
        # The three payloads are generated independently and could collide on
        # Event_ID; the global idempotency ledger would then drop the later
        # kinds as duplicates. Force cross-kind distinct Event_IDs so each kind
        # is genuinely persisted and can be checked for its machine tag.
        session = session.model_copy(update={"event_id": "sess-" + session.event_id})
        alert = alert.model_copy(update={"event_id": "alrt-" + alert.event_id})
        machine_event = machine_event.model_copy(
            update={"event_id": "mevt-" + machine_event.event_id}
        )

        async def scenario(repo, db):
            await repo.ingest_session(session)
            await repo.ingest_alert(alert, "http://store/img.jpg")
            await repo.ingest_machine_event(machine_event)

            srow = await db.fetch_one(
                "SELECT machine_id FROM sessions WHERE session_uuid = ?",
                (session.session_uuid,),
            )
            assert srow is not None
            assert srow["machine_id"] == session.machine_id
            assert srow["machine_id"] is not None and srow["machine_id"] != ""

            arow = await db.fetch_one(
                "SELECT machine_id FROM alerts WHERE event_id = ?", (alert.event_id,)
            )
            assert arow is not None
            assert arow["machine_id"] == alert.machine_id
            assert arow["machine_id"] is not None and arow["machine_id"] != ""

            merow = await db.fetch_one(
                "SELECT machine_id FROM machine_state_events WHERE event_id = ?",
                (machine_event.event_id,),
            )
            assert merow is not None
            assert merow["machine_id"] == machine_event.machine_id
            assert merow["machine_id"] is not None and merow["machine_id"] != ""

            # The idempotency ledger is machine-tagged for each kind too.
            for ev_id, expected in (
                (session.event_id, session.machine_id),
                (alert.event_id, alert.machine_id),
                (machine_event.event_id, machine_event.machine_id),
            ):
                lrow = await db.fetch_one(
                    "SELECT machine_id FROM ingested_events WHERE event_id = ?",
                    (ev_id,),
                )
                assert lrow is not None
                assert lrow["machine_id"] == expected

        run_scenario(migrated_template, scenario)

    @given(
        machine_id=_id_text,
        state=st.sampled_from(SESSION_STATES),
        worker_present=st.booleans(),
        duration=st.integers(min_value=0, max_value=1_000_000),
        light=st.sampled_from(LIGHT_STATES),
        health=st.sampled_from(CAMERA_HEALTH),
    )
    @_PBT
    def test_heartbeat_is_machine_tagged(
        self, migrated_template, machine_id, state, worker_present, duration, light, health
    ):
        # A produced Heartbeat carries a present, non-empty machine ID equal to
        # the originating machine ID (schema-level; heartbeats are not persisted).
        hb = Heartbeat(
            machine_id=machine_id,
            state=state,
            worker_present=worker_present,
            active_duration_seconds=duration,
            machine_light=light,
            camera_health=health,
        )
        assert hb.machine_id == machine_id
        assert hb.machine_id is not None and hb.machine_id != ""
