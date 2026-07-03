"""Data models for the shop floor tracker engine.

Contains SessionState enum and dataclasses for frame results,
session records, alerts, and live status.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime


class SessionState(Enum):
    """Possible states for a tracking session."""
    IDLE = "IDLE"
    OPENING = "OPENING"
    ACTIVE = "ACTIVE"
    GRACE = "GRACE"
    EXCEPTION = "EXCEPTION"
    ABANDONED = "ABANDONED"
    CLOSED = "CLOSED"


@dataclass
class FrameResult:
    """Result of processing a single video frame."""
    body_detected: bool
    badge_id: Optional[str]
    badge_bbox: Optional[tuple] = None  # (x1, y1, x2, y2)
    badge_static: bool = False


@dataclass
class SessionRecord:
    """Represents a tracked work session."""
    badge_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    active_duration_seconds: float = 0.0
    state: SessionState = SessionState.IDLE
    close_reason: Optional[str] = None


@dataclass
class AlertRecord:
    """Represents an anti-cheat or system alert."""
    badge_id: str
    alert_type: str  # "static_badge" | "no_body"
    message: Optional[str] = None
    resolved: bool = False
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class LiveStatus:
    """Current live status for the dashboard WebSocket broadcast."""
    state: SessionState = SessionState.IDLE
    badge_id: Optional[str] = None
    employee_name: Optional[str] = None
    active_duration_seconds: float = 0.0
    body_detected: bool = False
    badge_detected: bool = False
