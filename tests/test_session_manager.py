"""Tests for the presence-based session manager state machine.

Sessions are presence-based: they open when a person is detected and stable,
accumulate active time while present, and close after the grace period when
the person leaves. No badge reading.
"""

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.session_manager import SessionManager, WORKER_BADGE_ID
from engine.models import SessionState
from config import STABLE_FRAMES_REQUIRED, GRACE_PERIOD_SECONDS


def _advance(sm, body_detected, t, badge_static=False):
    """Helper to process a frame at an explicit time."""
    return sm.process_frame(body_detected=body_detected, badge_static=badge_static, now=t)


def _open_session(sm, start):
    """Drive the manager from IDLE through OPENING to ACTIVE.

    Returns the time after the session is opened.
    """
    t = start
    for _ in range(STABLE_FRAMES_REQUIRED):
        snap = _advance(sm, True, t)
        t += timedelta(seconds=1)
    assert sm.state == SessionState.ACTIVE
    return t, snap


class TestIdleToOpening:
    def test_body_detected_moves_to_opening(self):
        sm = SessionManager()
        t = datetime(2024, 1, 1, 8, 0, 0)
        snap = _advance(sm, True, t)
        assert sm.state == SessionState.OPENING
        assert snap['state'] == 'OPENING'

    def test_no_body_stays_idle(self):
        sm = SessionManager()
        t = datetime(2024, 1, 1, 8, 0, 0)
        snap = _advance(sm, False, t)
        assert sm.state == SessionState.IDLE
        assert snap['events'] == []


class TestOpeningToActive:
    def test_stable_body_opens_session(self):
        sm = SessionManager()
        t = datetime(2024, 1, 1, 8, 0, 0)
        opened_event = None
        for _ in range(STABLE_FRAMES_REQUIRED):
            snap = _advance(sm, True, t)
            t += timedelta(seconds=1)
            for e in snap['events']:
                if e['type'] == 'session_opened':
                    opened_event = e
        assert sm.state == SessionState.ACTIVE
        assert opened_event is not None
        assert opened_event['badge_id'] == WORKER_BADGE_ID

    def test_body_lost_during_opening_returns_idle(self):
        sm = SessionManager()
        t = datetime(2024, 1, 1, 8, 0, 0)
        _advance(sm, True, t)
        assert sm.state == SessionState.OPENING
        t += timedelta(seconds=1)
        _advance(sm, False, t)
        assert sm.state == SessionState.IDLE


class TestActiveAccumulation:
    def test_active_accumulates_duration(self):
        sm = SessionManager()
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        # Stay present for 10 more seconds
        t += timedelta(seconds=10)
        snap = _advance(sm, True, t)
        assert sm.state == SessionState.ACTIVE
        assert snap['active_duration_seconds'] > 0


class TestActiveToGrace:
    def test_body_loss_moves_to_grace(self):
        sm = SessionManager()
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        snap = _advance(sm, False, t)
        assert sm.state == SessionState.GRACE


class TestGraceToClosed:
    def test_grace_timeout_closes_session(self):
        sm = SessionManager()
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        # Body lost → GRACE
        _advance(sm, False, t)
        assert sm.state == SessionState.GRACE
        # Wait past the grace period
        t += timedelta(seconds=GRACE_PERIOD_SECONDS + 1)
        snap = _advance(sm, False, t)
        closed = [e for e in snap['events'] if e['type'] == 'session_closed']
        assert len(closed) == 1
        assert closed[0]['close_reason'] == 'grace_expired'
        assert closed[0]['badge_id'] == WORKER_BADGE_ID
        # Back to IDLE after close
        assert sm.state == SessionState.IDLE


class TestGraceToActive:
    def test_body_return_recovers_session(self):
        sm = SessionManager()
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        _advance(sm, False, t)
        assert sm.state == SessionState.GRACE
        # Body returns before timeout
        t += timedelta(seconds=5)
        _advance(sm, True, t)
        assert sm.state == SessionState.ACTIVE


class TestActiveToAbandoned:
    def test_static_worker_moves_to_abandoned(self):
        sm = SessionManager()
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        snap = _advance(sm, True, t, badge_static=True)
        assert sm.state == SessionState.ABANDONED
        alerts = [e for e in snap['events'] if e['type'] == 'alert_generated']
        assert len(alerts) == 1
        assert alerts[0]['alert_type'] == 'static_worker'
        assert alerts[0]['message'] == 'Worker present but no movement detected'


class TestAbandonedTransitions:
    def test_movement_resume_returns_active(self):
        sm = SessionManager()
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        _advance(sm, True, t, badge_static=True)
        assert sm.state == SessionState.ABANDONED
        # Movement resumes while present
        t += timedelta(seconds=1)
        _advance(sm, True, t, badge_static=False)
        assert sm.state == SessionState.ACTIVE

    def test_departure_moves_to_grace(self):
        sm = SessionManager()
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        _advance(sm, True, t, badge_static=True)
        assert sm.state == SessionState.ABANDONED
        # Person leaves
        t += timedelta(seconds=1)
        _advance(sm, False, t)
        assert sm.state == SessionState.GRACE


class TestSnapshot:
    def test_snapshot_fields(self):
        sm = SessionManager()
        t = datetime(2024, 1, 1, 8, 0, 0)
        snap = _advance(sm, True, t)
        assert 'state' in snap
        assert snap['badge_id'] == WORKER_BADGE_ID
        assert 'active_duration_seconds' in snap
        assert snap['body_detected'] is True
        assert snap['badge_detected'] is False
        assert 'events' in snap

    def test_default_signature_uses_clock(self):
        # process_frame works with only body_detected (no badge param, default clock)
        sm = SessionManager()
        snap = sm.process_frame(body_detected=True)
        assert snap['state'] == 'OPENING'


class TestMachineIdTagging:
    """Tests for per-machine session/event tagging (Requirement 3.1, 3.2, 3.3)."""

    def test_machine_id_in_session_opened_event(self):
        """session_opened events include machine_id when set."""
        sm = SessionManager(machine_id="M-01")
        t = datetime(2024, 1, 1, 8, 0, 0)
        t, snap = _open_session(sm, t)
        opened = [e for e in snap['events'] if e['type'] == 'session_opened']
        assert len(opened) == 1
        assert opened[0]['machine_id'] == "M-01"

    def test_machine_id_in_session_closed_event(self):
        """session_closed events include machine_id when set."""
        sm = SessionManager(machine_id="M-02")
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        # Body lost → GRACE
        _advance(sm, False, t)
        # Wait past grace period
        t += timedelta(seconds=GRACE_PERIOD_SECONDS + 1)
        snap = _advance(sm, False, t)
        closed = [e for e in snap['events'] if e['type'] == 'session_closed']
        assert len(closed) == 1
        assert closed[0]['machine_id'] == "M-02"

    def test_machine_id_in_alert_generated_event(self):
        """alert_generated events include machine_id when set."""
        sm = SessionManager(machine_id="M-03")
        start = datetime(2024, 1, 1, 8, 0, 0)
        t, _ = _open_session(sm, start)
        snap = _advance(sm, True, t, badge_static=True)
        alerts = [e for e in snap['events'] if e['type'] == 'alert_generated']
        assert len(alerts) == 1
        assert alerts[0]['machine_id'] == "M-03"

    def test_machine_id_in_snapshot(self):
        """The frame snapshot includes machine_id when set."""
        sm = SessionManager(machine_id="M-04")
        t = datetime(2024, 1, 1, 8, 0, 0)
        snap = _advance(sm, True, t)
        assert snap['machine_id'] == "M-04"

    def test_no_machine_id_when_not_set(self):
        """Events and snapshot omit machine_id when not configured."""
        sm = SessionManager()
        t = datetime(2024, 1, 1, 8, 0, 0)
        snap = _advance(sm, True, t)
        assert 'machine_id' not in snap

    def test_machine_id_property(self):
        """The machine_id property returns the configured ID."""
        sm = SessionManager(machine_id="M-05")
        assert sm.machine_id == "M-05"
        sm2 = SessionManager()
        assert sm2.machine_id is None


class TestMultiMachineIsolation:
    """Tests for independent state transitions across machines (Requirement 3.2)."""

    def test_independent_state_transitions(self):
        """Two SessionManagers for different machines operate independently."""
        sm1 = SessionManager(machine_id="M-01")
        sm2 = SessionManager(machine_id="M-02")
        t = datetime(2024, 1, 1, 8, 0, 0)

        # Open session on machine 1 only
        t1, _ = _open_session(sm1, t)
        assert sm1.state == SessionState.ACTIVE
        assert sm2.state == SessionState.IDLE

        # Machine 2 starts detection but machine 1 loses body
        _advance(sm2, True, t)
        assert sm2.state == SessionState.OPENING
        _advance(sm1, False, t1)
        assert sm1.state == SessionState.GRACE
        assert sm2.state == SessionState.OPENING

    def test_no_cross_machine_event_leakage(self):
        """Events from one machine don't appear in another's output."""
        sm1 = SessionManager(machine_id="M-01")
        sm2 = SessionManager(machine_id="M-02")
        start = datetime(2024, 1, 1, 8, 0, 0)

        # Open session on machine 1
        _open_session(sm1, start)

        # Machine 2 processes a frame — should have no events
        snap2 = _advance(sm2, False, start)
        assert snap2['events'] == []
        assert snap2['machine_id'] == "M-02"

    def test_concurrent_sessions_different_machines(self):
        """Both machines can have active sessions simultaneously."""
        sm1 = SessionManager(machine_id="M-01")
        sm2 = SessionManager(machine_id="M-02")
        start = datetime(2024, 1, 1, 8, 0, 0)

        _open_session(sm1, start)
        _open_session(sm2, start)

        assert sm1.state == SessionState.ACTIVE
        assert sm2.state == SessionState.ACTIVE

        # Closing one doesn't affect the other
        t = start + timedelta(seconds=STABLE_FRAMES_REQUIRED + 1)
        _advance(sm1, False, t)
        assert sm1.state == SessionState.GRACE
        assert sm2.state == SessionState.ACTIVE
