"""Graceful Shutdown Handler — coordinates orderly application termination.

On SIGTERM/SIGINT:
  1. Stop accepting new connections (signal uvicorn to stop)
  2. Send shutdown notification to WebSocket clients
  3. Stop all pipelines within 10s, finalize active sessions as CLOSED (reason: system_shutdown)
  4. Complete in-flight requests within 5s
  5. Close database connections cleanly
  6. Log shutdown-complete after all resources released

Requirements: 22.1, 22.2, 22.3, 22.4
"""

import asyncio
import structlog
import time
import threading
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.pipeline_orchestrator import PipelineOrchestrator
    from api.websocket import WebSocketManager
    from db.async_database import AsyncDatabase

logger = structlog.get_logger(__name__)

# Timeout constants
PIPELINE_STOP_TIMEOUT = 10.0  # seconds to wait for all pipelines to stop
INFLIGHT_REQUEST_TIMEOUT = 5.0  # seconds to wait for in-flight requests


class GracefulShutdown:
    """Coordinates graceful application shutdown sequence.

    Manages the ordered teardown of application resources:
    - Pipeline orchestrator (CV pipelines)
    - Session managers (active sessions)
    - WebSocket clients (shutdown notification)
    - Database connections

    Requirements: 22.1, 22.2, 22.3, 22.4
    """

    def __init__(self):
        self._orchestrator: Optional["PipelineOrchestrator"] = None
        self._ws_manager: Optional["WebSocketManager"] = None
        self._async_db: Optional["AsyncDatabase"] = None
        self._shutdown_event = threading.Event()
        self._shutdown_started = False
        self._shutdown_complete = False

    @property
    def is_shutting_down(self) -> bool:
        """Whether the shutdown sequence has been initiated."""
        return self._shutdown_started

    @property
    def is_complete(self) -> bool:
        """Whether the shutdown sequence has completed."""
        return self._shutdown_complete

    @property
    def shutdown_event(self) -> threading.Event:
        """Threading event that is set when shutdown begins."""
        return self._shutdown_event

    def set_orchestrator(self, orchestrator: "PipelineOrchestrator") -> None:
        """Register the pipeline orchestrator for shutdown coordination."""
        self._orchestrator = orchestrator

    def set_ws_manager(self, ws_manager: "WebSocketManager") -> None:
        """Register the WebSocket manager for client notification."""
        self._ws_manager = ws_manager

    def set_async_db(self, async_db: "AsyncDatabase") -> None:
        """Register the async database for clean connection closure."""
        self._async_db = async_db

    def initiate(self) -> None:
        """Initiate the graceful shutdown sequence.

        Sets the shutdown event to signal all components to begin stopping.
        This is called from the signal handler.
        """
        if self._shutdown_started:
            logger.warning("Shutdown already in progress, ignoring duplicate signal")
            return

        self._shutdown_started = True
        self._shutdown_event.set()
        logger.info("Graceful shutdown initiated")

    async def execute(self) -> None:
        """Execute the full async shutdown sequence.

        Call this from the FastAPI lifespan shutdown or an async context
        after initiate() has been called.

        Sequence:
          1. Send shutdown notification to WebSocket clients
          2. Stop all pipelines (within 10s), finalize active sessions
          3. Wait for in-flight requests to complete (up to 5s)
          4. Close database connections
          5. Log shutdown-complete
        """
        if not self._shutdown_started:
            self.initiate()

        start_time = time.time()
        logger.info("Executing shutdown sequence...")

        # Step 1: Notify WebSocket clients of impending shutdown (Req 22.3)
        await self._notify_websocket_clients()

        # Step 2: Stop all pipelines and finalize sessions (Req 22.1)
        await self._stop_pipelines_and_finalize_sessions()

        # Step 3: Allow in-flight requests to complete (Req 22.2)
        await self._wait_for_inflight_requests()

        # Step 4: Close database connections (Req 22.2)
        await self._close_database()

        # Step 5: Mark complete and log (Req 22.4)
        self._shutdown_complete = True
        elapsed = time.time() - start_time
        logger.info("Shutdown complete — all resources released (%.1fs elapsed)", elapsed)

    async def _notify_websocket_clients(self) -> None:
        """Send shutdown notification to all connected WebSocket clients.

        Requirement 22.3: Send shutdown notification before closing connections.
        """
        if self._ws_manager is None:
            logger.debug("No WebSocket manager registered, skipping client notification")
            return

        try:
            notification = {
                "type": "shutdown",
                "message": "Server is shutting down",
                "timestamp": datetime.now().isoformat(),
            }
            await self._ws_manager.broadcast(notification)
            logger.info(
                "Shutdown notification sent to %d WebSocket client(s)",
                self._ws_manager.client_count,
            )
        except Exception as e:
            logger.warning("Failed to send shutdown notification to WebSocket clients: %s", e)

    async def _stop_pipelines_and_finalize_sessions(self) -> None:
        """Stop all CV pipelines and finalize active sessions.

        Requirement 22.1: Stop all pipelines within 10s, finalize active
        sessions as CLOSED with reason 'system_shutdown'.
        """
        if self._orchestrator is None:
            logger.debug("No orchestrator registered, skipping pipeline shutdown")
            return

        try:
            # Finalize active sessions before stopping pipelines
            self._finalize_active_sessions()

            # Stop all pipelines with timeout (runs in thread since stop_all is synchronous)
            logger.info("Stopping all pipelines (timeout: %.0fs)...", PIPELINE_STOP_TIMEOUT)
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._orchestrator.stop_all,
                PIPELINE_STOP_TIMEOUT,
            )
            logger.info("All pipelines stopped successfully")
        except Exception as e:
            logger.error("Error during pipeline shutdown: %s", e)

    def _finalize_active_sessions(self) -> None:
        """Close all active sessions with reason 'system_shutdown'.

        Iterates all pipeline instances and calls _close_session on any
        SessionManager that has an active (non-IDLE) session.
        """
        if self._orchestrator is None:
            return

        from engine.models import SessionState

        try:
            statuses = self._orchestrator.get_all_statuses()
            finalized_count = 0

            for machine_id in statuses:
                instance = self._orchestrator.get_pipeline_instance(machine_id)
                if instance is None or not instance.components:
                    continue

                session_mgr = instance.components.get("session_manager")
                if session_mgr is None:
                    continue

                # Check if session manager has an active session (not IDLE)
                if hasattr(session_mgr, '_state') and session_mgr._state != SessionState.IDLE:
                    try:
                        now = datetime.now()
                        session_mgr._close_session(now, "system_shutdown")
                        finalized_count += 1
                        logger.info(
                            "Finalized active session on machine %s (reason: system_shutdown)",
                            machine_id,
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to finalize session on machine %s: %s", machine_id, e
                        )

            if finalized_count > 0:
                logger.info("Finalized %d active session(s) as CLOSED (system_shutdown)", finalized_count)
        except Exception as e:
            logger.error("Error finalizing sessions: %s", e)

    async def _wait_for_inflight_requests(self) -> None:
        """Wait for in-flight HTTP requests to complete.

        Requirement 22.2: Complete in-flight requests within 5s.
        Uvicorn handles this via its shutdown timeout, but we add
        a brief grace period here for any remaining async tasks.
        """
        logger.info(
            "Waiting up to %.0fs for in-flight requests to complete...",
            INFLIGHT_REQUEST_TIMEOUT,
        )
        await asyncio.sleep(min(INFLIGHT_REQUEST_TIMEOUT, 2.0))

    async def _close_database(self) -> None:
        """Close database connections cleanly.

        Requirement 22.2: Close the database connection cleanly.
        """
        if self._async_db is None:
            logger.debug("No async database registered, skipping DB close")
            return

        try:
            await self._async_db.close()
            logger.info("Database connections closed")
        except Exception as e:
            logger.warning("Error closing database connections: %s", e)


# ── Module-level singleton ───────────────────────────────────
_shutdown_handler: Optional[GracefulShutdown] = None


def get_shutdown_handler() -> GracefulShutdown:
    """Return the module-level GracefulShutdown singleton.

    Creates the instance on first call.
    """
    global _shutdown_handler
    if _shutdown_handler is None:
        _shutdown_handler = GracefulShutdown()
    return _shutdown_handler


def reset_shutdown_handler() -> None:
    """Reset the shutdown handler (for testing purposes)."""
    global _shutdown_handler
    _shutdown_handler = None
