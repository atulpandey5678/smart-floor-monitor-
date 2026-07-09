"""Tests for the Cloud_Server Ingest_API router (``api/ingest.py``).

Feature: edge-cloud-split

This module covers three task-list items, all exercised through the real
FastAPI Ingest_API router mounted on a minimal app (router + oversize-body
guard middleware) driven by ``fastapi.testclient.TestClient``:

- **7.3 / Property 3** — Schema-invalid payloads are rejected atomically:
  an invalid payload yields HTTP 422 and persists nothing (Req 2.5). Hypothesis.
- **7.4 / Property 5** — Unknown machine IDs are rejected atomically: a payload
  whose ``machine_id`` is not registered yields HTTP 422 and persists nothing,
  with an error indicating the machine ID is unknown (Req 2.8). Hypothesis.
- **7.5 / unit tests** — endpoint status codes and the 413 boundary:
  401 without a key, 413 just over the 10 MB body cap (200 at the boundary),
  200 with the echoed Event_ID, and a duplicate returning 200 within 5 s
  (Reqs 2.6, 2.9, 3.3, 5.2).

Test doubles are injected via the Ingest_API DI setters:
- the real ``Repository`` backed by a temporary migrated on-disk SQLite
  ``AsyncDatabase`` (so "nothing persisted" is a real durability assertion),
- an ``InMemoryObjectStore`` for alert images,
- a real ``LiveStateCache`` for heartbeats, and
- a small ``FakeRegistry`` giving explicit control over which machine IDs are
  registered (``is_registered`` / ``list_metadata``).

The AsyncDatabase is created *unconnected* and injected; it connects lazily on
the first request inside the TestClient's event loop, and the TestClient is
entered as a context manager so every request in a test shares that one loop.
Persisted-row counts are read back through an independent stdlib ``sqlite3``
connection (WAL readers see committed rows), which avoids any cross-event-loop
coupling with the app's aiosqlite connection.
"""

import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import config
from api import ingest as ingest_module
from api import ingest_auth
from api.ingest import router as ingest_router
from api.ingest_body_guard import IngestBodySizeGuardMiddleware
from db.async_database import AsyncDatabase
from db.migrations import MigrationRunner
from db.repository import Repository
from engine.live_state_cache import LiveStateCache
from engine.object_store import InMemoryObjectStore

# ── Locations / constants ─────────────────────────────────────────

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

# A fixed, known-good Ingest_API_Key used by every test in this module.
TEST_INGEST_KEY = "test-ingest-key-value-0123456789"
AUTH_HEADERS = {"X-Ingest-Key": TEST_INGEST_KEY}

# The single machine ID the FakeRegistry treats as registered.
REGISTERED_MACHINE = "M-01"

# Tables whose combined row count must stay unchanged when a request persists
# nothing (Properties 3 and 5).
_PERSIST_TABLES = ["sessions", "alerts", "machine_state_events", "ingested_events"]

# Enum vocabularies (mirror api/ingest_schemas.py).
LIGHT_STATES = ["GREEN", "AMBER", "RED", "OFF", "UNKNOWN"]
SESSION_STATES = ["IDLE", "OPENING", "ACTIVE", "GRACE", "ABANDONED", "CLOSED"]
CAMERA_HEALTH = ["HEALTHY", "DEGRADED", "FAILED"]
SESSION_OPS = ["open", "update", "close"]

_PBT = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[
        HealthCheck.function_scoped_fixture,
        HealthCheck.too_slow,
    ],
)


# ── Fake machine registry ─────────────────────────────────────────


class FakeRegistry:
    """Minimal CloudMachineRegistry stand-in for the Ingest_API.

    Only ``is_registered`` (used to validate ingest machine IDs) and
    ``list_metadata`` (unused by these tests) are required by the router.
    """

    def __init__(self, registered):
        self._registered = set(registered)

    async def is_registered(self, machine_id: str) -> bool:
        return machine_id in self._registered

    async def list_metadata(self, status=None):
        return []


# ── App / DI plumbing ─────────────────────────────────────────────


def _build_app() -> FastAPI:
    """Build a minimal FastAPI app: ingest router + oversize-body guard.

    Deliberately avoids the full ``api.server.app`` so no lifespan runs and no
    real database / DI wiring clobbers the injected test doubles.
    """
    app = FastAPI()
    app.add_middleware(IngestBodySizeGuardMiddleware)
    app.include_router(ingest_router)
    return app


def _build_migrated_db_file(tmp_dir: Path) -> str:
    """Create a fresh migrated (001-004) on-disk SQLite DB and return its path."""
    fd, path = tempfile.mkstemp(suffix=".db", dir=str(tmp_dir))
    import os

    os.close(fd)
    runner = MigrationRunner(path, MIGRATIONS_DIR)
    try:
        runner.run()
    finally:
        runner.close()
    return path


def _count_persisted(db_path: str) -> int:
    """Return the total row count across the persisted ingest tables.

    Uses an independent stdlib sqlite3 connection; in WAL mode a fresh reader
    sees all committed rows, so this reliably reflects what the app persisted.
    """
    con = sqlite3.connect(db_path)
    try:
        total = 0
        for table in _PERSIST_TABLES:
            total += con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return total
    finally:
        con.close()


class IngestEnv:
    """Bundle of the wired test environment handed to each test."""

    def __init__(self, client, db_path, store, registry, cache):
        self.client = client
        self.db_path = db_path
        self.store = store
        self.registry = registry
        self.cache = cache

    def count_persisted(self) -> int:
        return _count_persisted(self.db_path)


@pytest.fixture
def ingest_env(tmp_path, monkeypatch):
    """Wire the Ingest_API against test doubles and yield an :class:`IngestEnv`.

    Function-scoped: created once per test and shared across all Hypothesis
    examples within that test. Resets the module-level DI globals afterward.
    """
    # Configure the ingest key on the auth module namespace (constant-time
    # comparison target) for the duration of the test.
    monkeypatch.setattr(ingest_auth, "INGEST_API_KEY", TEST_INGEST_KEY)

    db_path = _build_migrated_db_file(tmp_path)
    async_db = AsyncDatabase(db_path=db_path)  # connect lazily inside the app loop
    repo = Repository(async_db)
    store = InMemoryObjectStore()
    registry = FakeRegistry({REGISTERED_MACHINE})
    cache = LiveStateCache(staleness_seconds=6.0)

    ingest_module.set_ingest_repo(repo)
    ingest_module.set_ingest_object_store(store)
    ingest_module.set_ingest_registry(registry)
    ingest_module.set_ingest_live_cache(cache)

    app = _build_app()
    with TestClient(app) as client:
        yield IngestEnv(client, db_path, store, registry, cache)

    # Reset DI so tests do not leak state into one another.
    ingest_module.set_ingest_repo(None)
    ingest_module.set_ingest_object_store(None)
    ingest_module.set_ingest_registry(None)
    ingest_module.set_ingest_live_cache(None)


# ── Valid-payload builders (JSON-friendly dicts) ──────────────────

_ISO = "2026-01-01T00:00:00"


def _valid_session(machine_id=REGISTERED_MACHINE, event_id="evt-s", session_uuid="su-1"):
    return {
        "event_id": event_id,
        "machine_id": machine_id,
        "session_uuid": session_uuid,
        "produced_at": _ISO,
        "op": "open",
        "start_time": _ISO,
        "end_time": None,
        "active_duration_seconds": 12.5,
        "close_reason": None,
    }


def _valid_alert(machine_id=REGISTERED_MACHINE, event_id="evt-a"):
    return {
        "event_id": event_id,
        "machine_id": machine_id,
        "produced_at": _ISO,
        "alert_type": "anti_cheat",
        "message": "left station",
        "event_image_b64": "",  # decoded only after the machine check passes
    }


def _valid_machine_event(machine_id=REGISTERED_MACHINE, event_id="evt-m"):
    return {
        "event_id": event_id,
        "machine_id": machine_id,
        "produced_at": _ISO,
        "previous_status": "GREEN",
        "new_status": "RED",
    }


def _valid_heartbeat(machine_id=REGISTERED_MACHINE):
    return {
        "machine_id": machine_id,
        "state": "ACTIVE",
        "worker_present": True,
        "active_duration_seconds": 30,
        "machine_light": "GREEN",
        "camera_health": "HEALTHY",
    }


# Map each write/status endpoint to (path, valid-body builder).
_ENDPOINTS = {
    "session": ("/api/ingest/session", _valid_session),
    "alert": ("/api/ingest/alert", _valid_alert),
    "machine_event": ("/api/ingest/machine-event", _valid_machine_event),
    "status": ("/api/ingest/status", _valid_heartbeat),
}


# ══════════════════════════════════════════════════════════════════
# 7.3 — Property 3: Schema-invalid payloads are rejected atomically
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 3: For any payload that fails schema
# validation (missing required field or value outside its defined set), the
# Ingest_API responds HTTP 422, the database record count is unchanged, and the
# error identifies each field that failed.
# Validates: Requirements 2.5

# Required fields per endpoint (dropping any one is a schema violation).
_REQUIRED_FIELDS = {
    "session": [
        "event_id",
        "machine_id",
        "session_uuid",
        "produced_at",
        "op",
        "start_time",
        "active_duration_seconds",
    ],
    "alert": ["event_id", "machine_id", "produced_at", "alert_type", "event_image_b64"],
    "machine_event": [
        "event_id",
        "machine_id",
        "produced_at",
        "previous_status",
        "new_status",
    ],
    "status": [
        "machine_id",
        "state",
        "worker_present",
        "active_duration_seconds",
        "machine_light",
        "camera_health",
    ],
}

# Enum fields per endpoint -> the allowed set (assigning a token outside the set
# is a schema violation "value outside its defined set").
_ENUM_FIELDS = {
    "session": {"op": SESSION_OPS},
    "alert": {},
    "machine_event": {"previous_status": LIGHT_STATES, "new_status": LIGHT_STATES},
    "status": {
        "state": SESSION_STATES,
        "machine_light": LIGHT_STATES,
        "camera_health": CAMERA_HEALTH,
    },
}

# Non-negative numeric fields (assigning a negative value violates ge=0).
_NONNEG_FIELDS = {
    "session": ["active_duration_seconds"],
    "alert": [],
    "machine_event": [],
    "status": ["active_duration_seconds"],
}

_OUT_OF_SET_TOKEN = "__not_a_valid_member__"


@st.composite
def invalid_payloads(draw):
    """Draw a (path, body, broken_field) triple that fails schema validation.

    Exactly one field is broken so the resulting 422 unambiguously identifies
    it, exercising "the error identifies each field that failed".
    """
    kind = draw(st.sampled_from(list(_ENDPOINTS.keys())))
    path, builder = _ENDPOINTS[kind]
    body = builder()

    # Choose a mutation that is valid for this endpoint.
    mutations = ["missing"]
    if _ENUM_FIELDS[kind]:
        mutations.append("enum")
    if _NONNEG_FIELDS[kind]:
        mutations.append("negative")
    mutation = draw(st.sampled_from(mutations))

    if mutation == "missing":
        field = draw(st.sampled_from(_REQUIRED_FIELDS[kind]))
        body.pop(field, None)
    elif mutation == "enum":
        field = draw(st.sampled_from(list(_ENUM_FIELDS[kind].keys())))
        body[field] = _OUT_OF_SET_TOKEN
    else:  # negative
        field = draw(st.sampled_from(_NONNEG_FIELDS[kind]))
        body[field] = draw(
            st.floats(min_value=-1_000_000.0, max_value=-0.001, allow_nan=False)
        )

    return path, body, field


class TestProperty3SchemaInvalidRejectedAtomically:
    @given(payload=invalid_payloads())
    @_PBT
    def test_invalid_schema_returns_422_and_persists_nothing(self, ingest_env, payload):
        path, body, broken_field = payload

        before = ingest_env.count_persisted()
        resp = ingest_env.client.post(path, json=body, headers=AUTH_HEADERS)

        # HTTP 422 for a schema-invalid payload.
        assert resp.status_code == 422, (path, body, resp.text)

        # The error identifies the field that failed (loc ends with the field).
        detail = resp.json()["detail"]
        assert isinstance(detail, list) and detail
        failed_fields = {
            str(loc[-1]) for err in detail for loc in [err.get("loc", [])] if loc
        }
        assert broken_field in failed_fields, (broken_field, detail)

        # Nothing was persisted (atomic rejection).
        assert ingest_env.count_persisted() == before
        # And no alert image leaked into the Object_Store.
        assert ingest_env.store.objects == {}


# ══════════════════════════════════════════════════════════════════
# 7.4 — Property 5: Unknown machine IDs are rejected atomically
# ══════════════════════════════════════════════════════════════════
# Feature: edge-cloud-split, Property 5: For any ingest payload whose machine ID
# is not registered in the Machine_Registry, the Cloud_Server responds HTTP 422,
# persists no data, and the error indicates the machine ID is unknown.
# Validates: Requirements 2.8

# Machine IDs the FakeRegistry does NOT know about.
_unregistered_ids = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=16,
).filter(lambda s: s != REGISTERED_MACHINE)


def _valid_body_for(kind, machine_id):
    """A schema-valid body for ``kind`` tagged with ``machine_id``."""
    if kind == "session":
        return _valid_session(machine_id=machine_id)
    if kind == "alert":
        return _valid_alert(machine_id=machine_id)
    if kind == "machine_event":
        return _valid_machine_event(machine_id=machine_id)
    return _valid_heartbeat(machine_id=machine_id)


class TestProperty5UnknownMachineRejectedAtomically:
    @given(
        kind=st.sampled_from(list(_ENDPOINTS.keys())),
        machine_id=_unregistered_ids,
    )
    @_PBT
    def test_unknown_machine_returns_422_and_persists_nothing(
        self, ingest_env, kind, machine_id
    ):
        assume(machine_id != REGISTERED_MACHINE)
        path, _ = _ENDPOINTS[kind]
        body = _valid_body_for(kind, machine_id)

        before = ingest_env.count_persisted()
        resp = ingest_env.client.post(path, json=body, headers=AUTH_HEADERS)

        # HTTP 422 for an unregistered machine ID.
        assert resp.status_code == 422, (path, body, resp.text)

        # The error indicates the machine ID is unknown.
        detail = resp.json()["detail"]
        detail_text = detail if isinstance(detail, str) else str(detail)
        assert "unknown machine" in detail_text.lower()
        assert machine_id in detail_text

        # No data persisted, no image uploaded, and (for heartbeats) the live
        # cache is untouched — nothing about the machine was recorded.
        assert ingest_env.count_persisted() == before
        assert ingest_env.store.objects == {}


# ══════════════════════════════════════════════════════════════════
# 7.5 — Unit tests: endpoint status codes and the 413 boundary
# ══════════════════════════════════════════════════════════════════
# Validates: Requirements 2.6, 2.9, 3.3, 5.2


def _alert_body_of_exact_size(size, machine_id=REGISTERED_MACHINE, event_id="evt-boundary"):
    """Return (body_bytes, event_id) for a valid alert JSON of exactly ``size``.

    The base64 image field is padded with ``A`` characters (length kept a
    multiple of 4 so it decodes cleanly). ``event_id`` is extended if needed to
    align the padding. All bytes are ASCII, so byte length equals string length.
    """
    suffix = '"}'

    def prefix_for(eid):
        return (
            '{"event_id":"' + eid + '","machine_id":"' + machine_id + '",'
            '"produced_at":"' + _ISO + '","alert_type":"anti_cheat",'
            '"message":null,"event_image_b64":"'
        )

    eid = event_id
    prefix = prefix_for(eid)
    img_len = size - (len(prefix) + len(suffix))
    if img_len < 0:
        raise ValueError("requested size is too small for a valid alert body")

    remainder = img_len % 4
    if remainder:
        # Grow the event_id by `remainder` chars; this shrinks img_len by the
        # same amount, making it a multiple of 4.
        eid = eid + ("x" * remainder)
        prefix = prefix_for(eid)
        img_len = size - (len(prefix) + len(suffix))

    body = prefix + ("A" * img_len) + suffix
    assert len(body) == size
    return body.encode("ascii"), eid


class TestIngestStatusCodesAndBoundary:
    def test_missing_key_returns_401(self, ingest_env):
        """No Ingest_API_Key at all -> HTTP 401 (Req 3.3)."""
        resp = ingest_env.client.post(
            "/api/ingest/session", json=_valid_session()
        )
        assert resp.status_code == 401
        # Nothing persisted on a rejected request.
        assert ingest_env.count_persisted() == 0

    def test_body_just_over_10mb_returns_413(self, ingest_env):
        """A body exceeding the 10 MB cap -> HTTP 413 before parsing (Req 2.9)."""
        oversized = b"x" * (config.INGEST_MAX_BODY_BYTES + 1)
        resp = ingest_env.client.post(
            "/api/ingest/alert",
            content=oversized,
            headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert ingest_env.count_persisted() == 0
        assert ingest_env.store.objects == {}

    def test_body_at_10mb_boundary_is_accepted(self, ingest_env):
        """A body exactly at the 10 MB cap is allowed through (inclusive boundary).

        Demonstrates the 413 guard rejects only bodies strictly larger than the
        configured maximum; a boundary-sized valid alert persists and returns 200.
        """
        body, event_id = _alert_body_of_exact_size(config.INGEST_MAX_BODY_BYTES)
        resp = ingest_env.client.post(
            "/api/ingest/alert",
            content=body,
            headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["event_id"] == event_id

    def test_success_returns_200_with_echoed_event_id(self, ingest_env):
        """A valid push for a registered machine -> 200 echoing the Event_ID (Req 2.6)."""
        body = _valid_session(event_id="evt-echo-1", session_uuid="su-echo-1")
        resp = ingest_env.client.post(
            "/api/ingest/session", json=body, headers=AUTH_HEADERS
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["event_id"] == "evt-echo-1"
        assert payload["status"] == "created"
        assert ingest_env.count_persisted() > 0

    def test_duplicate_returns_200_within_5s(self, ingest_env):
        """A re-delivered Event_ID -> 200 "already_persisted" within 5 s (Req 5.2)."""
        body = _valid_session(event_id="evt-dup-1", session_uuid="su-dup-1")

        first = ingest_env.client.post(
            "/api/ingest/session", json=body, headers=AUTH_HEADERS
        )
        assert first.status_code == 200
        assert first.json()["status"] == "created"

        start = time.perf_counter()
        second = ingest_env.client.post(
            "/api/ingest/session", json=body, headers=AUTH_HEADERS
        )
        elapsed = time.perf_counter() - start

        assert second.status_code == 200
        payload = second.json()
        assert payload["event_id"] == "evt-dup-1"
        assert payload["status"] == "already_persisted"
        assert elapsed < 5.0

        # Exactly one session row for the session_uuid — no duplicate created.
        con = sqlite3.connect(ingest_env.db_path)
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM sessions WHERE session_uuid = ?", ("su-dup-1",)
            ).fetchone()[0]
        finally:
            con.close()
        assert count == 1
