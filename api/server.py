"""FastAPI application setup with auth middleware and lifecycle events."""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from main import scheduled_backup, scheduled_report

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from config import API_HOST, API_PORT, DB_PATH
from db.database import Database, init_db
from db.repository import Repository

logger = logging.getLogger(__name__)

# Global instances (set during startup)
db = None
repo = None
_broadcast_task = None
_fastapi_loop = None
_scheduler = None

# Routes that do NOT require authentication
_AUTH_EXEMPT_PREFIXES = ("/auth/", "/ws", "/api/video_feed", "/api/stream", "/api/setup/")
_STATIC_EXTENSIONS = (".html", ".css", ".js", ".ico", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ttf")


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
    global db, repo, _broadcast_task, _fastapi_loop
    _fastapi_loop = asyncio.get_running_loop()

    # Startup
    logger.info("Starting Cologic Shop Floor Tracker API...")
    db = await init_db()
    repo = Repository(db)
    set_routes_repo(repo)
    logger.info(f"Database initialized at {DB_PATH}")

    # Register DB with auth module
    from api.auth import set_auth_db
    set_auth_db(db)

    # Initialize settings manager
    from engine.settings_manager import init_settings
    await init_settings(db)
    logger.info("Settings manager initialized")

    # Start WebSocket broadcast background task
    from api.websocket import broadcast_loop
    _broadcast_task = asyncio.create_task(broadcast_loop())
    logger.info("WebSocket broadcast loop started")

    global _scheduler
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(scheduled_backup, 'interval', hours=24)
    _scheduler.add_job(scheduled_report, 'interval', minutes=1)
    _scheduler.start()
    logger.info("APScheduler started (backup/report jobs scheduled)")


    yield

    # Shutdown
    logger.info("Shutting down...")
    if _broadcast_task:
        _broadcast_task.cancel()
        try:
            await _broadcast_task
        except asyncio.CancelledError:
            pass

    if _scheduler:
        _scheduler.shutdown()

    if db:
        await db.close()


app = FastAPI(
    title="Cologic Shop Floor Tracker",
    version="2.0.0",
    lifespan=lifespan,
)

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

# Import and include routes
from api.routes import router, set_repo as set_routes_repo  # noqa: E402
from api.auth import auth_router  # noqa: E402

app.include_router(router)
app.include_router(auth_router)

# Import and include WebSocket
from api.websocket import websocket_endpoint  # noqa: E402

app.add_api_websocket_route("/ws", websocket_endpoint)

# Serve dashboard static files (mount LAST so it doesn't override API routes)
dashboard_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
if os.path.isdir(dashboard_path):
    app.mount("/", StaticFiles(directory=dashboard_path, html=True), name="dashboard")
