"""Shop Floor Tracker — Application Entry Point.

Multi-machine architecture powered by PipelineOrchestrator.
Loads machine configurations from the MachineRegistry on startup,
starts pipelines for all active machines, and serves the FastAPI
dashboard + WebSocket. Falls back to legacy single-machine config
if no machines are registered.

Requirements: 2.1, 2.2, 3.1, 22.1
"""

import asyncio
import os
import signal
import sys
import threading
import time

import structlog
import uvicorn

from logging_config import setup_logging, bind_machine_id

# ── Structured Logging (must be configured before other imports log) ────
setup_logging(log_level="INFO", file_path="app.log")

from config import API_HOST, API_PORT, MACHINE_ID, RTSP_URL, DETECTION_ZONE, SHIFT_HOURS
from engine.pipeline_orchestrator import PipelineOrchestrator
from engine.shutdown import get_shutdown_handler

logger = structlog.get_logger(__name__)

try:
    from cv_pipeline.capture import FrameCapture
    from cv_pipeline.detector import PersonDetector
    from cv_pipeline.light_detector import LightDetector
    CV_AVAILABLE = True
except ImportError as e:
    CV_AVAILABLE = False
    logger.warning("CV pipeline not available, running API-only mode", error=str(e))

from db.database import init_db, Database
from db.async_database import AsyncDatabase
from db.repository import Repository
from api.routes import set_repo, set_state, set_frame_provider, set_light_detector
from api.websocket import set_state_provider, set_orchestrator
from engine.machine_registry import MachineRegistry
from engine import set_orchestrator as set_engine_orchestrator

# ── Shared broadcast state ───────────────────────────────────
_state_lock = threading.Lock()
_current_state = {
    "state": "IDLE",
    "employee_name": None,
    "active_duration_seconds": 0.0,
    "body_detected": False,
    "machine_id": MACHINE_ID,
    "session_start": None,
    "movement": "NO_DATA",
    "alert_type": None,
    "efficiency_percent": 0.0,
    "machine_light_status": "UNKNOWN",
    "camera_health": "offline",
    "_frame_ts": 0.0,
}

_shutdown_event = threading.Event()

_frame_lock_video = threading.Lock()
_annotated_frame: bytes = b""

_light_detector = None

# Module-level orchestrator reference (set during startup)
_orchestrator: PipelineOrchestrator = None


def get_light_detector():
    return _light_detector


def get_broadcast_state() -> dict:
    """Return a copy of the broadcast state with staleness and health applied."""
    with _state_lock:
        s = dict(_current_state)
    age = time.time() - s.get("_frame_ts", 0.0)
    if age > 3.0:
        s["body_detected"] = False
        s["movement"] = "NO_DATA"
    s["camera_health"] = "online" if age <= 3.0 else ("degraded" if age <= 10.0 else "offline")
    return s


def get_annotated_frame() -> bytes:
    with _frame_lock_video:
        return _annotated_frame


# ── Machine Loading ──────────────────────────────────────────

def _load_machine_configs_sync() -> list:
    """Load active machine configurations from the MachineRegistry.

    Uses a temporary async event loop to query the database.
    Returns a list of machine config dicts, or an empty list if none found.
    """
    from config import DB_PATH

    loop = asyncio.new_event_loop()
    try:
        async def _fetch():
            async_db = AsyncDatabase(db_path=DB_PATH)
            await async_db.connect()
            try:
                registry = MachineRegistry(async_db)
                machines = await registry.list_all(status="active")
                # Decrypt RTSP URLs for pipeline startup
                configs = []
                for m in machines:
                    rtsp_url = await registry.get_decrypted_url(m["machine_id"])
                    configs.append({
                        "machine_id": m["machine_id"],
                        "display_name": m["display_name"],
                        "rtsp_url": rtsp_url,
                        "detection_zone": m.get("detection_zone", "(0.0, 0.0, 1.0, 1.0)"),
                        "person_confidence_threshold": m.get("person_confidence_threshold", 0.60),
                        "light_zone": m.get("light_zone"),
                    })
                return configs
            finally:
                await async_db.close()

        return loop.run_until_complete(_fetch())
    except Exception as ex:
        logger.warning("Failed to load machines from registry, will use legacy config", error=str(ex))
        return []
    finally:
        loop.close()


def _get_legacy_machine_config() -> dict:
    """Build a machine config dict from legacy environment variables (MACHINE_ID, RTSP_URL)."""
    return {
        "machine_id": MACHINE_ID,
        "display_name": f"Machine {MACHINE_ID}",
        "rtsp_url": RTSP_URL,
        "detection_zone": DETECTION_ZONE,
        "person_confidence_threshold": 0.60,
        "light_zone": None,
    }


# ── Entry Point ──────────────────────────────────────────────

def main():
    global _orchestrator

    logger.info("=" * 58)
    logger.info("Cologic — Shop Floor Tracker v2.0")
    logger.info("Application starting", machine_id=MACHINE_ID, host=API_HOST, port=API_PORT)
    logger.info("=" * 58)

    # Pre-flight checks
    errors = []
    if not os.path.exists("yolov8n.pt"):
        errors.append("yolov8n.pt not found in project root")
    for f in ["dashboard/index.html", "dashboard/style.css", "dashboard/app.js"]:
        if not os.path.exists(f):
            errors.append(f"Dashboard file missing: {f}")
    if errors:
        for e in errors:
            logger.error("Pre-flight check failed", detail=e)
        sys.exit(1)
    logger.info("Pre-flight OK")

    # Security: warn if FERNET_KEY not set (non-fatal for dev mode)
    if not os.getenv("FERNET_KEY"):
        logger.warning(
            "FERNET_KEY not set — RTSP credentials will use an insecure dev key. "
            "Set FERNET_KEY in .env for production."
        )

    # DB init (synchronous sqlite3-based implementation for auth/settings)
    db = init_db()

    # Settings manager (async init — needs a temp loop)
    from engine.settings_manager import init_settings as _init_sm
    _loop = asyncio.new_event_loop()
    sm = _loop.run_until_complete(_init_sm(db))
    _loop.close()

    # Auth
    from api.auth import set_auth_db
    set_auth_db(db)

    repo = Repository(db)
    set_repo(repo)

    # Notifier
    try:
        from engine.notifier import init_notifier
        init_notifier(sm)
    except Exception as ex:
        logger.warning("Notifier init failed (non-fatal)", error=str(ex))

    # WebSocket — set legacy state provider for backward compatibility
    set_state_provider(get_broadcast_state)
    set_frame_provider(get_annotated_frame)

    # Daily auto-backup (runs 24 h after first start)
    def _do_backup():
        import shutil
        from datetime import datetime as _dt
        try:
            os.makedirs("backups", exist_ok=True)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            dest = f"backups/cologic_backup_{ts}.db"
            shutil.copy2("tracker.db", dest)
            logger.info("Auto-backup created", destination=dest)
            # Keep last 14
            files = sorted(f for f in os.listdir("backups") if f.endswith(".db"))
            for old in files[:-14]:
                try:
                    os.remove(os.path.join("backups", old))
                except Exception:
                    pass
        except Exception as ex:
            logger.error("Auto-backup failed", error=str(ex))
        # Reschedule
        t = threading.Timer(24 * 3600, _do_backup)
        t.daemon = True
        t.start()

    backup_timer = threading.Timer(24 * 3600, _do_backup)
    backup_timer.daemon = True
    backup_timer.start()

    # ── Pipeline Orchestrator Setup ──────────────────────────────
    if CV_AVAILABLE:
        # Create the orchestrator
        _orchestrator = PipelineOrchestrator(
            max_pipelines=8,
            restart_delay=10.0,
            max_restart_attempts=3,
        )

        # Register orchestrator in engine module for cross-module access
        set_engine_orchestrator(_orchestrator)

        # Wire orchestrator with WebSocket for multi-machine state broadcasting
        set_orchestrator(_orchestrator)

        # Load machine configurations from registry
        machine_configs = _load_machine_configs_sync()

        if not machine_configs:
            # No machines registered — only use legacy config if RTSP_URL is set
            if RTSP_URL:
                logger.info(
                    "No machines in registry — using legacy config (MACHINE_ID=%s)",
                    MACHINE_ID,
                )
                machine_configs = [_get_legacy_machine_config()]
            else:
                logger.info(
                    "No machines registered and no RTSP_URL configured. "
                    "Add a camera via the dashboard Camera Setup wizard."
                )
                machine_configs = []

        # Start pipelines for all active machines
        started_count = 0
        for config in machine_configs:
            success = _orchestrator.start_pipeline(config)
            if success:
                started_count += 1
                logger.info(
                    "Pipeline started for machine %s (%s)",
                    config["machine_id"],
                    config.get("display_name", ""),
                )
            else:
                logger.error(
                    "Failed to start pipeline for machine %s",
                    config["machine_id"],
                )

        logger.info(
            "PipelineOrchestrator: %d/%d pipelines started",
            started_count, len(machine_configs),
        )
    else:
        logger.warning("API-only mode — no CV pipeline (PipelineOrchestrator not started)")

    # ── Graceful Shutdown Wiring ─────────────────────────────────
    shutdown_handler = get_shutdown_handler()

    # Register orchestrator with shutdown handler for coordinated teardown (Req 22.1)
    if _orchestrator is not None:
        shutdown_handler.set_orchestrator(_orchestrator)

    def _shutdown(sig, frame):
        logger.info("Received shutdown signal", signal=str(sig))
        shutdown_handler.initiate()
        _shutdown_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # ── Start API Server ─────────────────────────────────────────
    try:
        uvicorn.run(
            "api.server:app",
            host=API_HOST,
            port=API_PORT,
            reload=False,
            log_level="warning",
        )
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_event.set()
        shutdown_handler.initiate()
        # Stop all pipelines via orchestrator
        if _orchestrator is not None:
            _orchestrator.stop_all(timeout=10.0)
        if not shutdown_handler.is_complete:
            logger.info("Shutdown complete")


if __name__ == "__main__":
    main()
