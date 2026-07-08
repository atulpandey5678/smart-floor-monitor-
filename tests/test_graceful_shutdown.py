"""Tests for engine/shutdown.py — GracefulShutdown handler.

Verifies the shutdown sequence: WebSocket notification, pipeline stop,
session finalization, database close, and completion logging.

Requirements: 22.1, 22.2, 22.3, 22.4
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from engine.shutdown import GracefulShutdown, get_shutdown_handler, reset_shutdown_handler


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level singleton before each test."""
    reset_shutdown_handler()
    yield
    reset_shutdown_handler()


@pytest.fixture
def shutdown():
    return GracefulShutdown()


@pytest.fixture
def mock_ws_manager():
    manager = MagicMock()
    manager.broadcast = AsyncMock()
    manager.client_count = 3
    return manager


@pytest.fixture
def mock_orchestrator():
    orchestrator = MagicMock()
    orchestrator.stop_all = MagicMock()
    orchestrator.get_all_statuses = MagicMock(return_value={})
    orchestrator.get_pipeline_instance = MagicMock(return_value=None)
    return orchestrator


@pytest.fixture
def mock_async_db():
    db = MagicMock()
    db.close = AsyncMock()
    return db


class TestGracefulShutdownInit:
    """Test GracefulShutdown initialization and state."""

    def test_initial_state(self, shutdown):
        assert not shutdown.is_shutting_down
        assert not shutdown.is_complete
        assert not shutdown.shutdown_event.is_set()

    def test_initiate_sets_state(self, shutdown):
        shutdown.initiate()
        assert shutdown.is_shutting_down
        assert shutdown.shutdown_event.is_set()

    def test_initiate_idempotent(self, shutdown):
        """Duplicate initiate calls should not error."""
        shutdown.initiate()
        shutdown.initiate()  # Should log warning but not fail
        assert shutdown.is_shutting_down


class TestGracefulShutdownRegistration:
    """Test component registration."""

    def test_set_orchestrator(self, shutdown, mock_orchestrator):
        shutdown.set_orchestrator(mock_orchestrator)
        assert shutdown._orchestrator is mock_orchestrator

    def test_set_ws_manager(self, shutdown, mock_ws_manager):
        shutdown.set_ws_manager(mock_ws_manager)
        assert shutdown._ws_manager is mock_ws_manager

    def test_set_async_db(self, shutdown, mock_async_db):
        shutdown.set_async_db(mock_async_db)
        assert shutdown._async_db is mock_async_db


class TestGracefulShutdownExecute:
    """Test the full shutdown execution sequence."""

    @pytest.mark.asyncio
    async def test_execute_sends_ws_notification(self, shutdown, mock_ws_manager):
        """Req 22.3: Send shutdown notification to WebSocket clients."""
        shutdown.set_ws_manager(mock_ws_manager)

        await shutdown.execute()

        mock_ws_manager.broadcast.assert_called_once()
        call_args = mock_ws_manager.broadcast.call_args[0][0]
        assert call_args["type"] == "shutdown"
        assert "message" in call_args
        assert "timestamp" in call_args

    @pytest.mark.asyncio
    async def test_execute_stops_all_pipelines(self, shutdown, mock_orchestrator):
        """Req 22.1: Stop all pipelines within 10s."""
        shutdown.set_orchestrator(mock_orchestrator)

        await shutdown.execute()

        mock_orchestrator.stop_all.assert_called_once_with(10.0)

    @pytest.mark.asyncio
    async def test_execute_closes_database(self, shutdown, mock_async_db):
        """Req 22.2: Close database connections cleanly."""
        shutdown.set_async_db(mock_async_db)

        await shutdown.execute()

        mock_async_db.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_marks_complete(self, shutdown):
        """Req 22.4: Log shutdown-complete after all resources released."""
        await shutdown.execute()

        assert shutdown.is_complete

    @pytest.mark.asyncio
    async def test_execute_full_sequence(self, shutdown, mock_ws_manager, mock_orchestrator, mock_async_db):
        """Verify the full shutdown sequence executes in order."""
        shutdown.set_ws_manager(mock_ws_manager)
        shutdown.set_orchestrator(mock_orchestrator)
        shutdown.set_async_db(mock_async_db)

        await shutdown.execute()

        # All steps should have been called
        mock_ws_manager.broadcast.assert_called_once()
        mock_orchestrator.stop_all.assert_called_once()
        mock_async_db.close.assert_called_once()
        assert shutdown.is_complete

    @pytest.mark.asyncio
    async def test_execute_without_components(self, shutdown):
        """Shutdown should complete even with no components registered."""
        await shutdown.execute()

        assert shutdown.is_complete

    @pytest.mark.asyncio
    async def test_execute_handles_ws_error(self, shutdown, mock_ws_manager, mock_async_db):
        """Shutdown continues even if WebSocket notification fails."""
        mock_ws_manager.broadcast = AsyncMock(side_effect=Exception("ws error"))
        shutdown.set_ws_manager(mock_ws_manager)
        shutdown.set_async_db(mock_async_db)

        await shutdown.execute()

        # Should still close DB and complete
        mock_async_db.close.assert_called_once()
        assert shutdown.is_complete

    @pytest.mark.asyncio
    async def test_execute_handles_orchestrator_error(self, shutdown, mock_orchestrator, mock_async_db):
        """Shutdown continues even if pipeline stop fails."""
        mock_orchestrator.stop_all.side_effect = Exception("stop error")
        shutdown.set_orchestrator(mock_orchestrator)
        shutdown.set_async_db(mock_async_db)

        await shutdown.execute()

        # Should still close DB and complete
        mock_async_db.close.assert_called_once()
        assert shutdown.is_complete


class TestSessionFinalization:
    """Test active session finalization during shutdown."""

    @pytest.mark.asyncio
    async def test_finalize_active_sessions(self, shutdown, mock_orchestrator):
        """Req 22.1: Finalize active sessions as CLOSED (reason: system_shutdown)."""
        from engine.models import SessionState

        # Mock a pipeline instance with an active session manager
        mock_session_mgr = MagicMock()
        mock_session_mgr._state = SessionState.ACTIVE
        mock_session_mgr._close_session = MagicMock()

        mock_instance = MagicMock()
        mock_instance.components = {"session_manager": mock_session_mgr}

        mock_orchestrator.get_all_statuses.return_value = {"M-01": {"status": "running"}}
        mock_orchestrator.get_pipeline_instance.return_value = mock_instance

        shutdown.set_orchestrator(mock_orchestrator)

        await shutdown.execute()

        # Session should have been closed with system_shutdown reason
        mock_session_mgr._close_session.assert_called_once()
        call_args = mock_session_mgr._close_session.call_args[0]
        assert isinstance(call_args[0], datetime)
        assert call_args[1] == "system_shutdown"

    @pytest.mark.asyncio
    async def test_skip_idle_sessions(self, shutdown, mock_orchestrator):
        """Sessions in IDLE state should not be finalized."""
        from engine.models import SessionState

        mock_session_mgr = MagicMock()
        mock_session_mgr._state = SessionState.IDLE
        mock_session_mgr._close_session = MagicMock()

        mock_instance = MagicMock()
        mock_instance.components = {"session_manager": mock_session_mgr}

        mock_orchestrator.get_all_statuses.return_value = {"M-01": {"status": "running"}}
        mock_orchestrator.get_pipeline_instance.return_value = mock_instance

        shutdown.set_orchestrator(mock_orchestrator)

        await shutdown.execute()

        mock_session_mgr._close_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_finalize_multiple_machines(self, shutdown, mock_orchestrator):
        """Multiple active machines should all have sessions finalized."""
        from engine.models import SessionState

        mock_session_mgr_1 = MagicMock()
        mock_session_mgr_1._state = SessionState.ACTIVE
        mock_session_mgr_1._close_session = MagicMock()

        mock_session_mgr_2 = MagicMock()
        mock_session_mgr_2._state = SessionState.GRACE
        mock_session_mgr_2._close_session = MagicMock()

        mock_instance_1 = MagicMock()
        mock_instance_1.components = {"session_manager": mock_session_mgr_1}

        mock_instance_2 = MagicMock()
        mock_instance_2.components = {"session_manager": mock_session_mgr_2}

        mock_orchestrator.get_all_statuses.return_value = {
            "M-01": {"status": "running"},
            "M-02": {"status": "running"},
        }
        mock_orchestrator.get_pipeline_instance.side_effect = lambda mid: {
            "M-01": mock_instance_1,
            "M-02": mock_instance_2,
        }[mid]

        shutdown.set_orchestrator(mock_orchestrator)

        await shutdown.execute()

        mock_session_mgr_1._close_session.assert_called_once()
        mock_session_mgr_2._close_session.assert_called_once()


class TestSingleton:
    """Test module-level singleton behavior."""

    def test_get_shutdown_handler_returns_same_instance(self):
        handler1 = get_shutdown_handler()
        handler2 = get_shutdown_handler()
        assert handler1 is handler2

    def test_reset_creates_new_instance(self):
        handler1 = get_shutdown_handler()
        reset_shutdown_handler()
        handler2 = get_shutdown_handler()
        assert handler1 is not handler2
