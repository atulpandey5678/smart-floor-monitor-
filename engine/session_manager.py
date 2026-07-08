"""Session state machine that manages worker session lifecycle.

Presence-based: tracks worker presence at a machine using person detection
and body movement only. No badge reading.

Implements the state machine with transitions:
  IDLE → OPENING → ACTIVE → GRACE → CLOSED
                          → ABANDONED

Pure logic class with injectable time for testability.
No I/O dependencies — the caller handles persistence and alerting.
"""

import structlog
from datetime import datetime
from typing import Optional, List, Callable

from engine.models import SessionState, LiveStatus
from config import STABLE_FRAMES_REQUIRED, GRACE_PERIOD_SECONDS

logger = structlog.get_logger(__name__)

# Constant badge identifier used to satisfy the DB schema (sessions.badge_id
# is NOT NULL). The system is presence-based and does not read badges.
WORKER_BADGE_ID = "WORKER"


class SessionManager:
    """Presence-based session state machine.

    Pure logic class with injectable time for testability.
    No I/O dependencies — the caller handles persistence and alerting.
    """

    def __init__(self, clock: Callable[[], datetime] = None, machine_id: Optional[str] = None):
        """Initialize the session manager.

        Args:
            clock: Optional callable that returns current datetime.
                   Defaults to datetime.now() if not provided.
            machine_id: Optional machine identifier. When set, all emitted
                        events (session_opened, session_closed, alert_generated)
                        are tagged with this machine_id for multi-machine isolation.
        """
        self._clock = clock or datetime.now
        self._machine_id = machine_id
        self._state = SessionState.IDLE
        self._current_badge_id: Optional[str] = None
        self._session_start: Optional[datetime] = None
        self._active_duration: float = 0.0
        self._stable_count: int = 0
        self._grace_start: Optional[datetime] = None
        self._last_frame_time: Optional[datetime] = None
        self._events: List[dict] = []  # accumulated events for caller

    @property
    def machine_id(self) -> Optional[str]:
        return self._machine_id

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def current_badge_id(self) -> Optional[str]:
        return self._current_badge_id

    @property
    def active_duration(self) -> float:
        return self._active_duration

    def process_frame(self, body_detected: bool, badge_static: bool = False,
                      now: datetime = None) -> dict:
        """Process a single frame's detection results and advance the state machine.

        Args:
            body_detected: Whether a person body was detected in frame.
            badge_static: Whether the body region has been static past the
                          movement timeout (from anti-cheat).
            now: Current time (injectable for testing). Defaults to clock().

        Returns:
            A snapshot dict with current state info for broadcasting, plus
            any events that occurred (session_opened, session_closed, alert_generated).
        """
        now = now or self._clock()
        self._events = []

        # Calculate time delta since last frame
        dt = 0.0
        if self._last_frame_time is not None:
            dt = (now - self._last_frame_time).total_seconds()
        self._last_frame_time = now

        # State transitions
        if self._state == SessionState.IDLE:
            self._handle_idle(body_detected, now)

        elif self._state == SessionState.OPENING:
            self._handle_opening(body_detected, now)

        elif self._state == SessionState.ACTIVE:
            self._handle_active(body_detected, badge_static, dt, now)

        elif self._state == SessionState.GRACE:
            self._handle_grace(body_detected, now)

        elif self._state == SessionState.ABANDONED:
            self._handle_abandoned(body_detected, badge_static, now)

        # Build snapshot for broadcasting
        snapshot = {
            'state': self._state.value,
            'badge_id': self._current_badge_id or WORKER_BADGE_ID,
            'active_duration_seconds': self._active_duration,
            'body_detected': body_detected,
            'badge_detected': False,
            'session_start': self._session_start.isoformat() if self._session_start else None,
            'events': self._events,
        }
        if self._machine_id is not None:
            snapshot['machine_id'] = self._machine_id
        return snapshot

    def _handle_idle(self, body_detected: bool, now: datetime):
        """IDLE: Waiting for a person to appear."""
        if body_detected:
            self._state = SessionState.OPENING
            self._current_badge_id = WORKER_BADGE_ID
            self._stable_count = 1
            logger.info("IDLE -> OPENING: body detected")

    def _handle_opening(self, body_detected: bool, now: datetime):
        """OPENING: Waiting for stable body presence before opening a session."""
        if not body_detected:
            # Lost body, return to IDLE
            self._state = SessionState.IDLE
            self._current_badge_id = None
            self._stable_count = 0
            logger.info("OPENING -> IDLE: body lost")
            return

        # Increment stability counter (body still present)
        self._stable_count += 1
        if self._stable_count >= STABLE_FRAMES_REQUIRED:
            # Stable presence confirmed — open session
            self._state = SessionState.ACTIVE
            self._session_start = now
            self._active_duration = 0.0
            event = {
                'type': 'session_opened',
                'badge_id': WORKER_BADGE_ID,
                'start_time': now,
            }
            if self._machine_id is not None:
                event['machine_id'] = self._machine_id
            self._events.append(event)
            logger.info(
                f"OPENING -> ACTIVE: body stable for "
                f"{STABLE_FRAMES_REQUIRED} frames"
            )

    def _handle_active(self, body_detected: bool, badge_static: bool,
                       dt: float, now: datetime):
        """ACTIVE: Session running, clock ticking while present and moving."""
        # Anti-cheat: present but no movement past timeout → ABANDONED
        if badge_static:
            self._state = SessionState.ABANDONED
            event = {
                'type': 'alert_generated',
                'badge_id': self._current_badge_id or WORKER_BADGE_ID,
                'alert_type': 'static_worker',
                'message': 'Worker present but no movement detected',
                'timestamp': now,
            }
            if self._machine_id is not None:
                event['machine_id'] = self._machine_id
            self._events.append(event)
            logger.warning("ACTIVE -> ABANDONED: no movement detected")
            return

        # Detection loss — body gone → GRACE
        if not body_detected:
            self._state = SessionState.GRACE
            self._grace_start = now
            logger.info("ACTIVE -> GRACE: body lost")
            return

        # Presence confirmed: body detected and moving → accumulate time
        self._active_duration += dt

    def _handle_grace(self, body_detected: bool, now: datetime):
        """GRACE: Temporary absence, waiting for recovery or timeout."""
        # Check if grace period expired
        elapsed = (now - self._grace_start).total_seconds()
        if elapsed >= GRACE_PERIOD_SECONDS:
            self._close_session(now, "grace_expired")
            logger.info("GRACE -> CLOSED: grace period expired")
            return

        # Recovery: body returned
        if body_detected:
            self._state = SessionState.ACTIVE
            self._grace_start = None
            logger.info("GRACE -> ACTIVE: presence recovered")

    def _handle_abandoned(self, body_detected: bool, badge_static: bool, now: datetime):
        """ABANDONED: Present but static. Waiting for movement or departure."""
        # Movement resumes while present → back to ACTIVE
        if body_detected and not badge_static:
            self._state = SessionState.ACTIVE
            logger.info("ABANDONED -> ACTIVE: movement resumed")
            return

        # Person left → go to GRACE
        if not body_detected:
            self._state = SessionState.GRACE
            self._grace_start = now
            logger.info("ABANDONED -> GRACE: body lost")

    def _close_session(self, now: datetime, reason: str):
        """Close the current session and emit a session_closed event."""
        event = {
            'type': 'session_closed',
            'badge_id': self._current_badge_id or WORKER_BADGE_ID,
            'start_time': self._session_start,
            'end_time': now,
            'active_duration_seconds': self._active_duration,
            'close_reason': reason,
        }
        if self._machine_id is not None:
            event['machine_id'] = self._machine_id
        self._events.append(event)
        logger.info(
            f"Session closed: duration={self._active_duration:.1f}s, reason={reason}"
        )
        # Reset state
        self._state = SessionState.CLOSED
        self._current_badge_id = None
        self._session_start = None
        self._active_duration = 0.0
        self._stable_count = 0
        self._grace_start = None
        # Transition back to IDLE immediately (CLOSED is transient)
        self._state = SessionState.IDLE

    def get_live_status(self) -> LiveStatus:
        """Get the current live status for dashboard broadcast."""
        return LiveStatus(
            state=self._state,
            badge_id=self._current_badge_id,
            active_duration_seconds=self._active_duration,
        )
