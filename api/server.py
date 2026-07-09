"""FastAPI application setup with auth middleware and lifecycle events."""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from config import API_HOST, API_PORT, DB_PATH
from db.database import Database, init_db
from db.async_database import init_async_db, close_async_db
from db.repository import Repository
from engine.shutdown import get_shutdown_handler

logger = structlog.get_logger(__name__)

# ── Rate Limiting ─────────────────────────────────────────────
# Global rate limit: 100 requests/minute per client IP (Requirement 9.1)
# Returns HTTP 429 with Retry-After header when exceeded (Requirement 9.4)
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])

# Global instances (set during startup)
db = None
repo = None
_broadcast_task = None
_fastapi_loop = None
_scheduler = None
_live_state_cache = None

# Routes that do NOT require authentication via the staff cookie AuthMiddleware.
# NOTE: /api/ingest/ is exempt from *cookie* auth because it uses a dedicated
# API-key path (api/ingest_auth.verify_ingest_key), not because it is public.
_AUTH_EXEMPT_PREFIXES = ("/auth/", "/ws", "/api/video_feed", "/api/stream", "/api/setup/",
                         "/api/v1/video_feed", "/api/v1/stream", "/api/v1/setup/",
                         "/api/ingest/",
                         "/health")
_STATIC_EXTENSIONS = (".html", ".css", ".js", ".ico", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ttf")


class CSRFMiddleware(BaseHTTPMiddleware):
    """Enforce CSRF double-submit cookie pattern on state-changing requests.

    Checks X-CSRF-Token header against csrf_token cookie for
    POST/PUT/DELETE requests on /api/* paths.

    Exempt: /auth/login, /auth/logout (no CSRF cookie yet on login).
    """

    _CSRF_EXEMPT_PATHS = ("/auth/login", "/auth/logout")
    _STATE_CHANGING_METHODS = ("POST", "PUT", "DELETE", "PATCH")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        method = request.method

        # Only check state-changing methods on API paths
        if method in self._STATE_CHANGING_METHODS and path.startswith("/api"):
            # Ingest endpoints use API-key auth (no CSRF cookie possible for
            # machine clients), so they are exempt from CSRF entirely.
            from api.ingest_auth import INGEST_PATH_PREFIX
            if path.startswith(INGEST_PATH_PREFIX):
                return await call_next(request)
            # Skip exempt paths
            if not any(path.endswith(p) for p in self._CSRF_EXEMPT_PATHS):
                from api.auth import verify_csrf_token
                try:
                    verify_csrf_token(request)
                except HTTPException as e:
                    return JSONResponse(
                        status_code=e.status_code,
                        content={"detail": e.detail},
                    )

        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """Protect all /api/* routes with session-cookie authentication.

    Exempt routes: /auth/*, /ws, /api/video_feed, and all static files.
    The /login.html page is served as a static file and is also exempt.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Always allow auth endpoints, websocket, and static assets
        if any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
            return await call_next(request)

        # Allow static files (by extension or root index)
        if path == "/" or any(path.endswith(ext) for ext in _STATIC_EXTENSIONS):
            return await call_next(request)

        # Protect only /api/* paths
        if path.startswith("/api/"):
            from api.auth import get_current_user
            user = await get_current_user(request)
            if not user:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Not authenticated"},
                )
            request.state.user = user

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown events."""
    global db, repo, _broadcast_task, _fastapi_loop, _live_state_cache
    _fastapi_loop = asyncio.get_running_loop()

    # Startup
    logger.info("Starting Cologic Shop Floor Tracker API...")

    # Run database migrations before anything else
    from db.migrations import MigrationRunner, MigrationError

    runner = MigrationRunner(DB_PATH)
    try:
        applied = runner.run()
        if applied:
            logger.info("Applied %d migration(s)", len(applied))
    except MigrationError as e:
        logger.critical("Migration failed: %s — aborting startup", e)
        raise
    finally:
        runner.close()

    db = init_db()  # synchronous — still needed for auth + settings_manager
    async_db = await init_async_db(DB_PATH)
    repo = Repository(async_db)  # Repository now uses async DB
    set_routes_repo(repo)
    logger.info(f"Database initialized at {DB_PATH} (sync + async)")

    # Wire the Ingest_API (Edge → Cloud). Share the async-DB-backed repository,
    # a credential-free CloudMachineRegistry, an in-memory Live_State_Cache, and
    # the configured Object_Store. The staleness sweeper flips entries to STALE
    # once they age past the interval.
    from engine.machine_registry import CloudMachineRegistry
    from engine.live_state_cache import get_live_state_cache
    from engine.snapshot_store import get_snapshot_store as _get_snapshot_store
    from api.ingest import (
        set_ingest_repo,
        set_ingest_live_cache,
        set_ingest_registry,
        set_ingest_snapshot_store,
    )

    global _live_state_cache
    # Use the single shared module singleton so the Ingest_API and the WebSocket
    # layer read/write the SAME cache instance (Req 6.6). Store it in the
    # module-level global so shutdown stops the correct sweeper.
    _live_state_cache = get_live_state_cache()
    cloud_registry = CloudMachineRegistry(async_db)
    set_ingest_repo(repo)
    set_ingest_registry(cloud_registry)
    set_ingest_live_cache(_live_state_cache)
    set_ingest_snapshot_store(_get_snapshot_store())  # share the singleton (Req 9.4)
    _live_state_cache.start_sweeper()
    logger.info("Ingest_API wired (repo + registry + live-state cache)")

    # Provide async DB to health check endpoints
    # Also wire in orchestrator if available (set in engine/__init__.py by main.py)
    from engine import get_orchestrator as _get_orch
    _orch = _get_orch()
    set_health_dependencies(pipeline_orchestrator=_orch, async_db=async_db)

    # Register DB with auth module
    from api.auth import set_auth_db, _hash_password
    set_auth_db(db)

    # Seed default admin user if no users exist (fixed credentials: cologic / cologic2026)
    try:
        row = db.fetch_one("SELECT COUNT(*) as cnt FROM users")
        if row and row["cnt"] == 0:
            pwd_hash = _hash_password("cologic2026")
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("cologic", pwd_hash, "admin"),
            )
            logger.info("Default admin user 'cologic' created")
    except Exception as e:
        logger.warning("Failed to seed default user (non-fatal): %s", e)

    # Initialize settings manager
    from engine.settings_manager import init_settings
    await init_settings(db)
    logger.info("Settings manager initialized")

    # Start WebSocket broadcast background task
    from api.websocket import broadcast_loop, ws_manager, set_live_state_cache
    _broadcast_task = asyncio.create_task(broadcast_loop())
    logger.info("WebSocket broadcast loop started")

    # Wire the SAME shared Live_State_Cache singleton into the WebSocket layer so
    # live tiles read from ingested Heartbeats and cache updates/liveness
    # transitions broadcast to clients (Req 6.6, 10.5). The singleton and its
    # sweeper are already set up in the Ingest_API wiring above, so we only wire
    # it into the WebSocket layer here — no second instance, no second sweeper.
    set_live_state_cache(_live_state_cache)
    logger.info("Live_State_Cache wired to WebSocket (shared singleton)")

    # Register components with graceful shutdown handler (Req 22.1, 22.2, 22.3)
    shutdown_handler = get_shutdown_handler()
    shutdown_handler.set_ws_manager(ws_manager)
    shutdown_handler.set_async_db(async_db)
    # Register orchestrator if available (may already be set from main.py)
    if _orch is not None:
        shutdown_handler.set_orchestrator(_orch)

    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Schedule data retention cleanup (Requirement 12.1)
    from engine.data_retention import schedule_retention_job
    schedule_retention_job(_scheduler, repo)

    _scheduler.start()
    logger.info("APScheduler started")


    yield

    # Shutdown — execute graceful shutdown sequence (Req 22.1, 22.2, 22.3, 22.4)
    logger.info("Shutting down...")

    if _broadcast_task:
        _broadcast_task.cancel()
        try:
            await _broadcast_task
        except asyncio.CancelledError:
            pass

    # Stop the shared Live_State_Cache staleness sweeper exactly once. The
    # module global points at the singleton wired during startup.
    if _live_state_cache is not None:
        try:
            await _live_state_cache.stop_sweeper()
        except Exception as e:
            logger.warning("Failed to stop Live_State_Cache sweeper: %s", e)

    if _scheduler:
        _scheduler.shutdown()

    # Execute the coordinated shutdown (notifications, pipeline stop, DB close)
    shutdown_handler = get_shutdown_handler()
    await shutdown_handler.execute()

    # Close sync DB if still open (fallback — shutdown handler closes async)
    if db:
        db.close()


app = FastAPI(
    title="Cologic Shop Floor Tracker",
    version="2.0.0",
    lifespan=lifespan,
)

# Attach rate limiter to app state and register 429 handler
app.state.limiter = limiter


def _custom_rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return HTTP 429 with Retry-After header (Requirement 9.4)."""
    # Extract retry-after seconds from the rate limit window (default 60s for per-minute)
    retry_after = 60  # seconds remaining in the current window
    try:
        # Try to get actual window reset from limiter
        if hasattr(request.state, "view_rate_limit") and request.state.view_rate_limit:
            window_stats = limiter.limiter.get_window_stats(
                request.state.view_rate_limit[0], *request.state.view_rate_limit[1]
            )
            import time
            retry_after = max(1, int(window_stats[0] - time.time()))
    except Exception:
        pass  # Fall back to default 60 seconds

    response = JSONResponse(
        status_code=429,
        content={"error": f"Rate limit exceeded: {exc.detail}"},
        headers={"Retry-After": str(retry_after)},
    )
    return response


app.add_exception_handler(RateLimitExceeded, _custom_rate_limit_handler)

# CORS (localhost development only — auth cookie handles real security)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth middleware (must come after CORS)
app.add_middleware(AuthMiddleware)

# CSRF middleware for state-changing API requests
app.add_middleware(CSRFMiddleware)

# Ingest oversize-body guard (outermost): reject /api/ingest/* bodies over the
# configured max with HTTP 413 before parsing or persistence (Requirement 2.9).
from api.ingest_body_guard import IngestBodySizeGuardMiddleware  # noqa: E402

app.add_middleware(IngestBodySizeGuardMiddleware)

# Import and include routes
from api.routes import router, set_repo as set_routes_repo  # noqa: E402
from api.auth import auth_router  # noqa: E402
from api.routes_machines import router as machines_router  # noqa: E402
from api.health import router as health_router, set_health_dependencies  # noqa: E402

# Mount main routes at both /api (backward-compatible) and /api/v1 (versioned)
# Requirement 20.1: All REST endpoints available at /api/v1/
# Requirement 20.4: /api/ remains as alias for backward compatibility
app.include_router(router, prefix="/api")
app.include_router(router, prefix="/api/v1")

# Mount machine routes at both /api/v1 and /api for consistency
app.include_router(machines_router, prefix="/api/v1")
app.include_router(machines_router, prefix="/api")

app.include_router(auth_router)

# Mount the Ingest_API (Edge → Cloud). The router carries its own /api/ingest
# prefix and API-key dependency; it is exempt from cookie auth + CSRF (see
# _AUTH_EXEMPT_PREFIXES and CSRFMiddleware) and guarded by the oversize-body
# middleware already installed above.
from api.ingest import router as ingest_router  # noqa: E402

app.include_router(ingest_router)

# Mount health check at root level (unauthenticated, no /api prefix)
# Requirement 14.1, 14.2, 14.3, 14.4
app.include_router(health_router)

# Import and include WebSocket
from api.websocket import websocket_endpoint  # noqa: E402

app.add_api_websocket_route("/ws", websocket_endpoint)

# Serve dashboard static files (mount LAST so it doesn't override API routes)
dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
if os.path.isdir(dashboard_path):
    app.mount("/", StaticFiles(directory=dashboard_path, html=True), name="dashboard")
