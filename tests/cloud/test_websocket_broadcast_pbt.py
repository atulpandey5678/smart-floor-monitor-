"""Property-based tests for the Cloud_Server WebSocket broadcast path.

Covers one correctness property from the design's "Correctness Properties"
section, exercising the end-to-end wiring from a Live_State_Cache update through
``api.websocket.set_live_state_cache`` (which points the cache's broadcast
callback at ``broadcast_live_state`` -> ``ws_manager.broadcast_machine_state``)
out to subscribed Dashboard clients:

- Property 24: Cache update broadcasts to subscribed clients.
  Validates Requirement 6.6.

Each Hypothesis example drives the fully-async stack through ``asyncio.run`` on a
fresh event loop, using fake WebSocket clients that capture the JSON frames they
receive. The shared ``ws_manager`` singleton and the wired Live_State_Cache are
reset before every example so state never leaks between cases.
"""

from __future__ import annotations

import asyncio
import json

from hypothesis import given, settings
from hypothesis import strategies as st

from api import websocket as ws_mod
from api.websocket import ws_manager
from api.ingest_schemas import Heartbeat
from engine.live_state_cache import LiveStateCache, LIVENESS_STALE


# ── Deterministic clock ───────────────────────────────────────────


class FakeClock:
    """A controllable time source returning seconds for the cache."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += float(dt)


# ── Fake WebSocket client ─────────────────────────────────────────


class FakeWebSocket:
    """A stand-in for a Dashboard WebSocket client.

    Captures every text frame ``ws_manager`` sends it. ``accept``/``close`` are
    provided so it can be registered through the real ``ws_manager.connect``
    path exactly like a live client.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, text: str) -> None:
        self.messages.append(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True

    # Convenience: parsed state_update envelopes for a given machine.
    def state_updates_for(self, machine_id: str) -> list[dict]:
        out = []
        for raw in self.messages:
            msg = json.loads(raw)
            if msg.get("type") == "state_update" and msg.get("machine_id") == machine_id:
                out.append(msg)
        return out


# ── Enum vocabularies (mirror api/ingest_schemas.py) ──────────────

SESSION_STATES = ["IDLE", "OPENING", "ACTIVE", "GRACE", "ABANDONED", "CLOSED"]
LIGHT_STATES = ["GREEN", "AMBER", "RED", "OFF", "UNKNOWN"]
CAMERA_HEALTHS = ["HEALTHY", "DEGRADED", "FAILED"]

machine_ids = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
    min_size=1,
    max_size=8,
)


def _make_heartbeat(machine_id: str, draw) -> Heartbeat:
    return Heartbeat(
        machine_id=machine_id,
        state=draw(st.sampled_from(SESSION_STATES)),
        worker_present=draw(st.booleans()),
        active_duration_seconds=draw(st.integers(min_value=0, max_value=100_000)),
        machine_light=draw(st.sampled_from(LIGHT_STATES)),
        camera_health=draw(st.sampled_from(CAMERA_HEALTHS)),
    )


@st.composite
def broadcast_scenarios(draw):
    """Generate a set of machine IDs, a target machine, client subscriptions,
    and a valid Heartbeat for the target machine.

    A client's subscription is one of: all machines ("*"), an arbitrary subset
    of the known machine IDs (possibly empty), so both subscribed and
    non-subscribed clients are exercised.
    """
    ids = draw(st.lists(machine_ids, min_size=1, max_size=5, unique=True))
    target = draw(st.sampled_from(ids))

    def sub_strategy():
        return st.one_of(
            st.just({"*"}),
            st.sets(st.sampled_from(ids), min_size=0, max_size=len(ids)),
        )

    subscriptions = draw(st.lists(sub_strategy(), min_size=1, max_size=6))
    heartbeat = _make_heartbeat(target, draw)
    return ids, target, subscriptions, heartbeat


def _reset_ws_manager() -> None:
    """Clear the shared ws_manager singleton so examples never leak state."""
    ws_manager._clients.clear()
    ws_manager._subscriptions.clear()
    ws_manager._has_subscribed.clear()


# ── Property 24 ────────────────────────────────────────────────────
# Feature: edge-cloud-split, Property 24: Cache update broadcasts to subscribed
# clients — every Dashboard client subscribed to that machine receives exactly
# one state-update envelope carrying the updated status for that machine ID.
# Validates: Requirement 6.6


@given(scenario=broadcast_scenarios())
@settings(max_examples=150, deadline=None)
def test_cache_update_broadcasts_to_subscribed_clients(scenario) -> None:
    """A valid Heartbeat update reaches exactly the subscribed clients."""
    ids, target, subscriptions, heartbeat = scenario

    async def run() -> None:
        _reset_ws_manager()
        clock = FakeClock(1000.0)
        cache = LiveStateCache(staleness_seconds=6, clock=clock)
        # Wire the cache so its broadcast callback -> broadcast_live_state ->
        # ws_manager.broadcast_machine_state (Requirement 6.6).
        ws_mod.set_live_state_cache(cache)
        try:
            clients: list[tuple[FakeWebSocket, set]] = []
            for subs in subscriptions:
                fake = FakeWebSocket()
                connected = await ws_manager.connect(fake)
                assert connected is True
                if subs:
                    ws_manager.subscribe(fake, set(subs))
                clients.append((fake, set(subs)))

            # Apply a valid Heartbeat for the target machine -> triggers one
            # broadcast to every client subscribed to that machine.
            await cache.update_from_heartbeat(heartbeat)

            for fake, subs in clients:
                subscribed = "*" in subs or target in subs
                updates = fake.state_updates_for(target)
                if subscribed:
                    # Exactly one state-update envelope for the target machine.
                    assert len(updates) == 1, (
                        f"subscribed client expected 1 update, got {len(updates)}"
                    )
                    payload = updates[0]["payload"]
                    assert payload["machine_id"] == target
                    assert payload["state"] == heartbeat.state
                    assert payload["worker_present"] == heartbeat.worker_present
                    assert (
                        payload["active_duration_seconds"]
                        == heartbeat.active_duration_seconds
                    )
                    assert payload["machine_light"] == heartbeat.machine_light
                    assert payload["camera_health"] == heartbeat.camera_health
                else:
                    # Non-subscribed clients receive nothing for the target.
                    assert updates == [], (
                        "non-subscribed client must not receive the update"
                    )
        finally:
            ws_mod.set_live_state_cache(None)
            _reset_ws_manager()

    asyncio.run(run())


@given(scenario=broadcast_scenarios())
@settings(max_examples=150, deadline=None)
def test_sweeper_liveness_transition_broadcasts_to_subscribed_clients(scenario) -> None:
    """A sweeper STALE transition also reaches exactly the subscribed clients."""
    ids, target, subscriptions, heartbeat = scenario

    async def run() -> None:
        _reset_ws_manager()
        clock = FakeClock(1000.0)
        cache = LiveStateCache(staleness_seconds=6, clock=clock)
        ws_mod.set_live_state_cache(cache)
        try:
            clients: list[tuple[FakeWebSocket, set]] = []
            for subs in subscriptions:
                fake = FakeWebSocket()
                connected = await ws_manager.connect(fake)
                assert connected is True
                if subs:
                    ws_manager.subscribe(fake, set(subs))
                clients.append((fake, set(subs)))

            # Seed a LIVE entry, then discard the heartbeat broadcast so we can
            # isolate the sweeper's STALE-transition broadcast.
            await cache.update_from_heartbeat(heartbeat)
            for fake, _ in clients:
                fake.messages.clear()

            # Age the entry past the staleness interval and sweep.
            clock.advance(cache.staleness_seconds + 1.0)
            changed = await cache.sweep_once()
            assert len(changed) == 1
            assert changed[0].liveness == LIVENESS_STALE

            for fake, subs in clients:
                subscribed = "*" in subs or target in subs
                updates = fake.state_updates_for(target)
                if subscribed:
                    assert len(updates) == 1
                    assert updates[0]["payload"]["liveness"] == LIVENESS_STALE
                else:
                    assert updates == []
        finally:
            ws_mod.set_live_state_cache(None)
            _reset_ws_manager()

    asyncio.run(run())
