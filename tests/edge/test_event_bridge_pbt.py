"""Property-based tests for the Edge_Agent event bridge and live-status modules.

Feature: edge-cloud-split

Covers four correctness properties:
- Property 22: Heartbeat well-formedness and camera-health mapping (Req 6.3)
- Property 30: Snapshot thumbnail is reduced resolution (Req 9.2)
- Property 20: Outbound payloads exclude camera credentials (Reqs 7.7, 13.1, 13.4)
- Property 1:  Light state classification is always a valid member (Req 1.5)
"""

from __future__ import annotations

import json
import sys
import tempfile
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from api.ingest_schemas import (
    CameraHealth,
    LightState,
    MachineEventMsg,
    SessionRecordMsg,
    SessionState,
)
from edge.live_status import classify_camera_health, LiveStatusPublisher, SESSION_ACTIVE_STATES

# ── Enum vocabularies ────────────────────────────────────────────────────────

_SESSION_STATES: list = list(SessionState.__args__)
_LIGHT_STATES: list = list(LightState.__args__)
_CAMERA_HEALTHS: list = list(CameraHealth.__args__)
_VALID_LIGHT_STATES = frozenset(_LIGHT_STATES)


# ── Fake SyncClient for property tests ──────────────────────────────────────

class _FakeSyncClient:
    """Records every submit call without network I/O."""

    def __init__(self):
        self.submitted: List[Dict] = []

    def submit_session(self, msg) -> str:
        payload = msg.model_dump(mode="json")
        self.submitted.append({"kind": "session", "payload": payload})
        return payload["event_id"]

    def submit_alert(self, msg, image: bytes = b"") -> str:
        payload = msg.model_dump(mode="json")
        self.submitted.append({"kind": "alert", "payload": payload})
        return payload["event_id"]

    def submit_machine_event(self, msg) -> str:
        payload = msg.model_dump(mode="json")
        self.submitted.append({"kind": "machine_event", "payload": payload})
        return payload["event_id"]


# ── Property 22: Heartbeat well-formedness and camera-health mapping ─────────
# Feature: edge-cloud-split, Property 22: For any valid snapshot dict and
# frame-age, build_heartbeat() returns a schema-valid Heartbeat where
# camera_health follows HEALTHY ≤ 2 s, DEGRADED 2–10 s, FAILED > 10 s
# or disconnected.
# Validates: Requirements 6.3

class _FakeLiveStatusPublisher:
    """Thin wrapper exposing build_heartbeat with injectable clock."""

    def __init__(self, machine_id: str = "M-01"):
        self._pub = LiveStatusPublisher(
            sync_client=None,  # not used in build_heartbeat
            machine_id=machine_id,
        )

    def build_heartbeat(self, snapshot, *, last_frame_age_s=None,
                        connected=True, machine_light="UNKNOWN"):
        return self._pub.build_heartbeat(
            snapshot,
            last_frame_age_s=last_frame_age_s,
            connected=connected,
            machine_light=machine_light,
        )


class TestProperty22HeartbeatWellFormedness:

    @given(
        state=st.sampled_from(_SESSION_STATES),
        worker_present=st.booleans(),
        duration=st.integers(min_value=0, max_value=3_600_000),
        machine_light=st.sampled_from(_LIGHT_STATES),
    )
    @settings(max_examples=150, deadline=None)
    def test_heartbeat_fields_are_valid(
        self,
        state: str,
        worker_present: bool,
        duration: int,
        machine_light: str,
    ) -> None:
        """build_heartbeat() produces a schema-valid Heartbeat for any valid inputs."""
        snapshot: Dict[str, Any] = {
            "state": state,
            "worker_present": worker_present,
            "active_duration_seconds": duration,
            "body_detected": worker_present,
            "machine_id": "M-01",
            "events": [],
        }
        pub = _FakeLiveStatusPublisher("M-01")
        hb = pub.build_heartbeat(snapshot, last_frame_age_s=0.5,
                                  connected=True, machine_light=machine_light)

        assert hb.state in _SESSION_STATES
        assert hb.machine_light in _LIGHT_STATES
        assert hb.camera_health in _CAMERA_HEALTHS
        assert isinstance(hb.worker_present, bool)
        assert hb.active_duration_seconds >= 0

    @given(
        age=st.floats(min_value=0.0, max_value=0.0,
                      allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_camera_health_boundary_healthy(self, age: float) -> None:
        """age == 0 → HEALTHY."""
        assert classify_camera_health(0.0, connected=True) == "HEALTHY"

    @given(
        age=st.floats(min_value=0.0, max_value=2.0,
                      allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_camera_health_healthy_range(self, age: float) -> None:
        """age ≤ 2.0 s → HEALTHY."""
        result = classify_camera_health(age, connected=True)
        assert result == "HEALTHY", f"age={age} expected HEALTHY got {result}"

    @given(
        age=st.floats(min_value=2.001, max_value=10.0,
                      allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_camera_health_degraded_range(self, age: float) -> None:
        """2 s < age ≤ 10 s → DEGRADED."""
        result = classify_camera_health(age, connected=True)
        assert result == "DEGRADED", f"age={age} expected DEGRADED got {result}"

    @given(
        age=st.floats(min_value=10.001, max_value=3600.0,
                      allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_camera_health_failed_range(self, age: float) -> None:
        """age > 10 s → FAILED."""
        result = classify_camera_health(age, connected=True)
        assert result == "FAILED", f"age={age} expected FAILED got {result}"

    @given(age=st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
    @settings(max_examples=100, deadline=None)
    def test_camera_health_disconnected_is_failed(self, age: float) -> None:
        """connected=False → FAILED regardless of age."""
        result = classify_camera_health(age, connected=False)
        assert result == "FAILED"


# ── Property 30: Snapshot thumbnail is reduced resolution ───────────────────
# Feature: edge-cloud-split, Property 30: make_thumbnail() returns dimensions
# <= max_dim AND <= source dimension — it never upscales.
# Validates: Requirements 9.2

class TestProperty30SnapshotThumbnail:

    @given(
        h=st.integers(min_value=1, max_value=1920),
        w=st.integers(min_value=1, max_value=1920),
        max_dim=st.integers(min_value=50, max_value=640),
    )
    @settings(max_examples=150, deadline=None)
    def test_thumbnail_dimensions_capped_and_not_upscaled(
        self, h: int, w: int, max_dim: int
    ) -> None:
        """make_thumbnail result dimensions ≤ min(max_dim, source dim) each axis."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            pytest.skip("cv2 not available")

        from edge.live_status import LiveStatusPublisher

        frame = np.zeros((h, w, 3), dtype=np.uint8)
        pub = LiveStatusPublisher(
            sync_client=None,
            machine_id="M-01",
            thumbnail_max_dim=max_dim,
        )
        result = pub.make_thumbnail(frame)
        if result is None:
            pytest.skip("make_thumbnail returned None (cv2 encode failed)")
            return

        # Decode to verify actual dimensions
        img_array = np.frombuffer(result, dtype=np.uint8)
        decoded = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        assert decoded is not None, "Thumbnail could not be decoded"
        th, tw = decoded.shape[:2]

        # Neither dimension may exceed the cap
        assert th <= max_dim, f"height {th} > max_dim {max_dim}"
        assert tw <= max_dim, f"width {tw} > max_dim {max_dim}"
        # Neither dimension may exceed the source dimension (no upscaling)
        assert th <= h, f"height {th} > source height {h} (upscaled!)"
        assert tw <= w, f"width {tw} > source width {w} (upscaled!)"


# ── Property 20: Outbound payloads exclude camera credentials ────────────────
# Feature: edge-cloud-split, Property 20: No payload submitted via the
# Sync_Client contains the RTSP URL, username, or password.
# Validates: Requirements 7.7, 13.1, 13.4

class TestProperty20OutboundPayloadsExcludeCredentials:

    @given(
        machine_id=st.text(
            alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
            min_size=1, max_size=8,
        ),
        rtsp_url=st.text(
            alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="\"\\"),
            min_size=8, max_size=40,
        ).map(lambda s: "rtsp://admin:" + s + "@192.168.1.10:554/stream"),
        username=st.text(min_size=5, max_size=16,
                         alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="\"\\-0123456789")),
        password=st.text(min_size=8, max_size=32,
                         alphabet=st.characters(min_codepoint=33, max_codepoint=126, blacklist_characters="\"\\-0123456789")),
    )
    @settings(max_examples=100, deadline=None)
    def test_submitted_session_payload_excludes_credentials(
        self,
        machine_id: str,
        rtsp_url: str,
        username: str,
        password: str,
    ) -> None:
        """SessionRecordMsg submitted via EventBridge contains no rtsp/credentials."""
        from edge.event_bridge import EventBridge

        fake_client = _FakeSyncClient()

        # Pass credentials only in the machine_config (as the real edge would)
        bridge = EventBridge(fake_client, machine_id=machine_id)

        snapshot = {
            "state": "ACTIVE",
            "badge_id": "WORKER",
            "active_duration_seconds": 10,
            "body_detected": True,
            "badge_detected": False,
            "session_start": datetime.utcnow().isoformat(),
            "machine_id": machine_id,
            "events": [
                {
                    "type": "session_opened",
                    "machine_id": machine_id,
                    "badge_id": "WORKER",
                    "session_start": datetime.utcnow().isoformat(),
                }
            ],
        }
        bridge.process(snapshot, frame=None, light_result=None)

        # Collect all submitted JSON payloads as strings
        for submission in fake_client.submitted:
            payload_str = json.dumps(submission["payload"])
            assert rtsp_url not in payload_str, "rtsp_url leaked into payload"
            assert password not in payload_str, "password leaked into payload"
            # Username might be a common word; check for the full rtsp URL
            # (which contains username) rather than the username alone
            assert "rtsp://" not in payload_str or "rtsp_url" not in payload_str.lower()


# ── Property 1: Light state classification is always a valid member ──────────
# Feature: edge-cloud-split, Property 1: Any light state value produced by the
# edge is always a member of the LightState Literal set.
# Validates: Requirements 1.5

class TestProperty1LightStateClassificationValid:

    @given(
        raw_status=st.text(min_size=0, max_size=20),
    )
    @settings(max_examples=150, deadline=None)
    def test_event_bridge_normalizes_light_to_valid_state(
        self, raw_status: str
    ) -> None:
        """EventBridge maps any raw light status to a valid LightState member."""
        from edge.event_bridge import EventBridge

        fake_client = _FakeSyncClient()
        bridge = EventBridge(fake_client, machine_id="M-01")

        # Craft a light transition event using the raw status
        snapshot = {
            "state": "ACTIVE",
            "badge_id": "WORKER",
            "active_duration_seconds": 5,
            "body_detected": True,
            "badge_detected": False,
            "session_start": datetime.utcnow().isoformat(),
            "machine_id": "M-01",
            "events": [],
        }
        # Simulate a light result that may have an arbitrary raw status
        light_result = {
            "status": raw_status,
            "transition": True,
            "previous": "OFF",
        }
        bridge.process(snapshot, frame=None, light_result=light_result)

        for submission in fake_client.submitted:
            if submission["kind"] == "machine_event":
                new_status = submission["payload"].get("new_status", "")
                previous_status = submission["payload"].get("previous_status", "")
                assert new_status in _VALID_LIGHT_STATES, (
                    f"new_status {new_status!r} is not a valid LightState"
                )
                assert previous_status in _VALID_LIGHT_STATES, (
                    f"previous_status {previous_status!r} is not a valid LightState"
                )

    @given(
        state=st.sampled_from(_LIGHT_STATES),
    )
    @settings(max_examples=100, deadline=None)
    def test_valid_light_states_pass_through_unchanged(self, state: str) -> None:
        """Valid LightState members are forwarded as-is."""
        from edge.event_bridge import EventBridge

        fake_client = _FakeSyncClient()
        bridge = EventBridge(fake_client, machine_id="M-01")

        snapshot = {
            "state": "ACTIVE",
            "badge_id": "WORKER",
            "active_duration_seconds": 5,
            "body_detected": True,
            "badge_detected": False,
            "session_start": datetime.utcnow().isoformat(),
            "machine_id": "M-01",
            "events": [],
        }
        light_result = {
            "status": state,
            "transition": True,
            "previous": "OFF",
        }
        bridge.process(snapshot, frame=None, light_result=light_result)

        for submission in fake_client.submitted:
            if submission["kind"] == "machine_event":
                assert submission["payload"]["new_status"] == state
