"""Pytest configuration and shared fixtures for the Cologic test suite.

Provides:
- In-memory AsyncDatabase fixture with full schema
- Mock RTSP capture that yields fake frames
- Mock PipelineOrchestrator for testing without real cameras
- Async event loop configuration

Requirements: 23.5
"""

import asyncio
import os
import sys
import threading
from typing import Any, AsyncGenerator, Dict, Generator, Optional
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import pytest_asyncio

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Schema SQL (combined from migrations) ────────────────────────────────────

_SCHEMA_SQL = """
-- Tables
CREATE TABLE IF NOT EXISTS employees (
    badge_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    badge_id TEXT NOT NULL,
    machine_id TEXT NOT NULL DEFAULT 'M-01',
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP,
    active_duration_seconds REAL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'ACTIVE',
    close_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (badge_id) REFERENCES employees(badge_id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    badge_id TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT,
    resolved INTEGER DEFAULT 0,
    root_cause TEXT,
    machine_id TEXT DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS machine_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app_settings (
    section TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (section, key)
);

CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    rtsp_url_encrypted TEXT NOT NULL,
    detection_zone TEXT NOT NULL DEFAULT '(0.0, 0.0, 1.0, 1.0)',
    ocr_zone TEXT NOT NULL DEFAULT '{"x1": 0.30, "y1": 0.10, "x2": 0.70, "y2": 0.55}',
    person_confidence_threshold REAL NOT NULL DEFAULT 0.60,
    light_zone TEXT DEFAULT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_sessions_machine_start ON sessions(machine_id, start_time);
CREATE INDEX IF NOT EXISTS idx_sessions_badge ON sessions(badge_id);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved_created ON alerts(resolved, created_at);
CREATE INDEX IF NOT EXISTS idx_alerts_machine_id ON alerts(machine_id);
CREATE INDEX IF NOT EXISTS idx_machine_events_machine_ts ON machine_state_events(machine_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_machines_status ON machines(status);
CREATE INDEX IF NOT EXISTS idx_machines_machine_id ON machines(machine_id);
"""


# ── Async Database Fixture ───────────────────────────────────────────────────

@pytest_asyncio.fixture
async def async_db():
    """Create an in-memory AsyncDatabase with full schema applied.

    Yields an AsyncDatabase instance backed by `:memory:` SQLite.
    The schema from all migrations is applied before yielding.
    Connection is closed after the test completes.
    """
    from db.async_database import AsyncDatabase

    db = AsyncDatabase(db_path=":memory:")
    await db.connect()

    # Apply full schema
    conn = db._connection
    await conn.executescript(_SCHEMA_SQL)
    await conn.commit()

    yield db

    await db.close()


# ── Mock RTSP Capture Fixture ────────────────────────────────────────────────

class MockFrameCapture:
    """Mock RTSP capture that yields synthetic frames without a real camera.

    Produces 640x480 BGR frames with random noise. Simulates the
    FrameCapture interface used by CV pipelines.
    """

    def __init__(self, width: int = 640, height: int = 480, fps: int = 10):
        self.width = width
        self.height = height
        self.fps = fps
        self._frame_count = 0
        self._running = False
        self._stop_event = threading.Event()
        self._latest_frame: Optional[np.ndarray] = None

    def start(self):
        """Simulate starting the capture (no-op for mock)."""
        self._running = True
        self._stop_event.clear()
        # Generate an initial frame immediately
        self._latest_frame = self._generate_frame()

    def stop(self):
        """Simulate stopping the capture."""
        self._running = False
        self._stop_event.set()

    def get_frame(self) -> Optional[np.ndarray]:
        """Return a synthetic frame (640x480 BGR with random noise)."""
        if not self._running:
            return None
        self._frame_count += 1
        self._latest_frame = self._generate_frame()
        return self._latest_frame

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def is_opened(self) -> bool:
        return self._running

    def _generate_frame(self) -> np.ndarray:
        """Generate a synthetic BGR frame with random pixel values."""
        return np.random.randint(0, 255, (self.height, self.width, 3), dtype=np.uint8)


@pytest.fixture
def mock_frame_capture() -> MockFrameCapture:
    """Provide a MockFrameCapture instance for tests needing fake RTSP frames."""
    capture = MockFrameCapture()
    return capture


# ── Mock Pipeline Orchestrator Fixture ───────────────────────────────────────

@pytest.fixture
def mock_orchestrator():
    """Create a PipelineOrchestrator with a no-op pipeline factory.

    The factory immediately returns (simulating a pipeline that runs
    until stop_event is set), enabling testing of orchestrator lifecycle
    without real RTSP connections or ML model loading.
    """
    from engine.pipeline_orchestrator import PipelineOrchestrator, PipelineInstance

    def _mock_pipeline_factory(
        machine_config: Dict[str, Any],
        stop_event: threading.Event,
        instance: PipelineInstance,
    ) -> None:
        """Mock pipeline that blocks until stop_event is set."""
        stop_event.wait()

    orchestrator = PipelineOrchestrator(
        max_pipelines=8,
        restart_delay=0.1,  # Fast restarts for tests
        max_restart_attempts=3,
        pipeline_factory=_mock_pipeline_factory,
    )

    yield orchestrator

    # Cleanup: stop all pipelines after test
    orchestrator.stop_all(timeout=2.0)


# ── Mock Pipeline (single instance) ─────────────────────────────────────────

@pytest.fixture
def mock_pipeline():
    """Create a mock single CV pipeline with pre-configured components.

    Returns a dict simulating pipeline components (capture, detector,
    session_manager) for unit tests that don't need the full orchestrator.
    """
    from engine.session_manager import SessionManager

    capture = MockFrameCapture()
    session_mgr = SessionManager(machine_id="M-TEST")

    # Mock detector that always returns no detection
    detector = MagicMock()
    detector.detect.return_value = (False, None)

    pipeline = {
        "capture": capture,
        "detector": detector,
        "session_manager": session_mgr,
        "machine_id": "M-TEST",
        "machine_config": {
            "machine_id": "M-TEST",
            "rtsp_url": "rtsp://mock:554/stream",
            "display_name": "Test Machine",
            "person_confidence_threshold": 0.6,
            "detection_zone": [0.0, 0.0, 1.0, 1.0],
        },
    }

    return pipeline
