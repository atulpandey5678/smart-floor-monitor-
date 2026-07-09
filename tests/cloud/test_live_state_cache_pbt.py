"""Property-based tests for engine/live_state_cache.py (edge-cloud-split).

Covers two correctness properties from the design's "Correctness Properties"
section, exercising the Live_State_Cache with a deterministic injected clock so
staleness/liveness are testable without real sleeps:

- Property 23: Live_State_Cache updates on valid, is invariant on invalid.
  Validates Requirements 6.4, 6.5.
- Property 25: Liveness classification (LIVE / STALE / UNKNOWN).
  Validates Requirements 6.7, 6.8.

The cache is fully async (guarded by an ``asyncio.Lock``); each Hypothesis
example drives it through ``asyncio.run`` so every example runs on a fresh,
isolated event loop with a fresh cache instance.
"""

from __future__ import annotations

import asyncio

from hypothesis import given, settings
from hypothesis import strategies as st

from engine.live_state_cache import (
    LIVENESS_LIVE,
    LIVENESS_STALE,
    LIVENESS_UNKNOWN,
    LiveStateCache,
)

# ── Deterministic clock ───────────────────────────────────────────


class FakeClock:
    """A controllable time source returning seconds for the cache."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


# ── Heartbeat enum vocabularies (mirrors api/ingest_schemas.py) ────

SESSION_STATES = ["IDLE", "OPENING", "ACTIVE", "GRACE", "ABANDONED", "CLOSED"]
LIGHT_STATES = ["GREEN", "AMBER", "RED", "OFF", "UNKNOWN"]
CAMERA_HEALTHS = ["HEALTHY", "DEGRADED", "FAILED"]

_ENUM_SETS = {
    "state": SESSION_STATES,
    "machine_light": LIGHT_STATES,
    "camera_health": CAMERA_HEALTHS,
}

# Uppercase alphanumeric machine IDs; deliberately excludes lowercase so the
# literal "never-seen-xyz" sentinel below can never collide with a generated ID.
machine_ids = st.text(
    alphabet=st.characters(whitelist_categories=(), whitelist_characters="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"),
    min_size=1,
    max_size=12,
).filter(lambda s: s != "never-seen-xyz")


@st.composite
def heartbeat_dicts(draw, machine_id: str | None = None) -> dict:
    """A schema-valid Heartbeat payload as a raw dict."""
    mid = machine_id if machine_id is not None else draw(machine_ids)
    return {
        "machine_id": mid,
        "state": draw(st.sampled_from(SESSION_STATES)),
        "worker_present": draw(st.booleans()),
        "active_duration_seconds": draw(st.integers(min_value=0, max_value=100_000)),
        "machine_light": draw(st.sampled_from(LIGHT_STATES)),
        "camera_health": draw(st.sampled_from(CAMERA_HEALTHS)),
    }


@st.composite
def invalid_heartbeat_dicts(draw, machine_id: str) -> dict:
    """A Heartbeat payload guaranteed to fail schema validation.

    Keeps ``machine_id`` present so the invariance check can target that
    machine's existing cache entry. Invalidity comes from one of:
    a missing required field, an out-of-set enum value, or a negative duration.
    """
    base = draw(heartbeat_dicts(machine_id=machine_id))
    kind = draw(st.sampled_from(["missing", "bad_enum", "negative"]))

    if kind == "missing":
        # Remove any required field except machine_id.
        field = draw(
            st.sampled_from(
                [
                    "state",
                    "worker_present",
                    "active_duration_seconds",
                    "machine_light",
                    "camera_health",
                ]
            )
        )
        base.pop(field)
    elif kind == "bad_enum":
        field = draw(st.sampled_from(list(_ENUM_SETS.keys())))
        allowed = set(_ENUM_SETS[field])
        base[field] = draw(st.text(min_size=1, max_size=8).filter(lambda s: s not in allowed))
    else:  # negative duration violates Field(ge=0)
        base["active_duration_seconds"] = draw(st.integers(min_value=-100_000, max_value=-1))

    return base


@st.composite
def valid_then_invalid(draw) -> tuple[dict, dict]:
    """A (valid, invalid) heartbeat pair sharing one machine ID."""
    mid = draw(machine_ids)
    return draw(heartbeat_dicts(machine_id=mid)), draw(invalid_heartbeat_dicts(machine_id=mid))


# ── Property 23 ────────────────────────────────────────────────────
# Feature: edge-cloud-split, Property 23: Live_State_Cache updates on valid, is
# invariant on invalid.
# Validates: Requirements 6.4, 6.5


@given(hb=heartbeat_dicts())
@settings(max_examples=150, deadline=None)
def test_valid_heartbeat_updates_cache_entry(hb: dict) -> None:
    """A valid Heartbeat updates the entry to its values + a received time."""

    async def scenario() -> None:
        clock = FakeClock(500.0)
        cache = LiveStateCache(staleness_seconds=6, clock=clock)

        ok, returned = await cache.apply_raw_heartbeat(hb)
        assert ok is True
        assert returned is not None

        stored = await cache.get(hb["machine_id"])
        assert stored is not None
        assert stored.machine_id == hb["machine_id"]
        assert stored.state == hb["state"]
        assert stored.worker_present == hb["worker_present"]
        assert stored.active_duration_seconds == hb["active_duration_seconds"]
        assert stored.machine_light == hb["machine_light"]
        assert stored.camera_health == hb["camera_health"]
        # Received timestamp recorded from the injected clock (Req 6.4).
        assert stored.received_at == 500.0

    asyncio.run(scenario())


@given(pair=valid_then_invalid())
@settings(max_examples=150, deadline=None)
def test_invalid_heartbeat_leaves_existing_entry_unchanged(pair: tuple[dict, dict]) -> None:
    """An invalid Heartbeat leaves the existing entry untouched (Req 6.5)."""
    valid, invalid = pair

    async def scenario() -> None:
        clock = FakeClock(500.0)
        cache = LiveStateCache(staleness_seconds=6, clock=clock)

        ok, _ = await cache.apply_raw_heartbeat(valid)
        assert ok is True
        before = await cache.get(valid["machine_id"])
        assert before is not None

        # Advance the clock so an erroneous update would change received_at.
        clock.advance(1.0)

        ok2, returned2 = await cache.apply_raw_heartbeat(invalid)
        assert ok2 is False
        assert returned2 is None

        after = await cache.get(valid["machine_id"])
        assert after is not None
        assert after.machine_id == before.machine_id
        assert after.state == before.state
        assert after.worker_present == before.worker_present
        assert after.active_duration_seconds == before.active_duration_seconds
        assert after.machine_light == before.machine_light
        assert after.camera_health == before.camera_health
        assert after.received_at == before.received_at

    asyncio.run(scenario())


# ── Property 25 ────────────────────────────────────────────────────
# Feature: edge-cloud-split, Property 25: Liveness classification
# (LIVE / STALE / UNKNOWN).
# Validates: Requirements 6.7, 6.8


@given(
    hb=heartbeat_dicts(),
    staleness=st.floats(min_value=2.0, max_value=300.0),
    age=st.floats(min_value=0.0, max_value=600.0),
)
@settings(max_examples=200, deadline=None)
def test_liveness_classification(hb: dict, staleness: float, age: float) -> None:
    """Never-seen -> UNKNOWN; within interval -> LIVE; aged past -> STALE."""

    async def scenario() -> None:
        clock = FakeClock(1000.0)
        cache = LiveStateCache(staleness_seconds=staleness, clock=clock)
        mid = hb["machine_id"]

        # UNKNOWN and STALE are always distinct classifications (Req 6.8).
        assert LIVENESS_UNKNOWN != LIVENESS_STALE

        # A machine with no entry reports UNKNOWN, distinct from STALE (6.8).
        assert await cache.get_liveness("never-seen-xyz") == LIVENESS_UNKNOWN

        # Record a valid heartbeat at t0.
        ok, _ = await cache.apply_raw_heartbeat(hb)
        assert ok is True

        # Freshly seen -> LIVE.
        assert await cache.get_liveness(mid) == LIVENESS_LIVE

        # Age the entry and reclassify against the (clamped) staleness interval.
        # Derive the expectation from the actual recorded timestamp and the
        # post-advance clock so the comparison uses the identical float
        # arithmetic the cache performs (now - received_at), avoiding a
        # spurious mismatch exactly at the staleness boundary.
        stored = await cache.get(mid)
        assert stored is not None
        received_at = stored.received_at
        clock.advance(age)
        elapsed = clock() - received_at
        expected = LIVENESS_STALE if elapsed > cache.staleness_seconds else LIVENESS_LIVE
        assert await cache.get_liveness(mid) == expected

    asyncio.run(scenario())
