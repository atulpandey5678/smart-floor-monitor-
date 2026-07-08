"""Health check endpoints for monitoring and orchestration tools.

Exposes unauthenticated endpoints:
  GET /health       — system status, DB connectivity, per-machine pipeline status
  GET /health/ready — readiness probe (migrations applied + at least one pipeline running)

Requirements: 14.1, 14.2, 14.3, 14.4
"""

import structlog
import time
from typing import Any, Dict

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])

# ── Module-level references (set during app startup) ─────────
_pipeline_orchestrator = None
_async_db = None


def set_health_dependencies(pipeline_orchestrator=None, async_db=None):
    """Inject dependencies from app startup.

    Args:
        pipeline_orchestrator: PipelineOrchestrator instance (or None if not yet started).
        async_db: AsyncDatabase instance for DB connectivity checks.
    """
    global _pipeline_orchestrator, _async_db
    if pipeline_orchestrator is not None:
        _pipeline_orchestrator = pipeline_orchestrator
    if async_db is not None:
        _async_db = async_db


async def _check_db() -> Dict[str, Any]:
    """Probe database connectivity and measure response time.

    Returns dict with keys: reachable (bool), response_time_ms (float | None).
    """
    if _async_db is None:
        return {"reachable": False, "response_time_ms": None}

    start = time.perf_counter()
    try:
        row = await _async_db.fetch_one("SELECT 1")
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {"reachable": row is not None, "response_time_ms": round(elapsed_ms, 2)}
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.warning("Health check DB probe failed: %s", exc)
        return {"reachable": False, "response_time_ms": round(elapsed_ms, 2)}


def _get_pipeline_statuses() -> Dict[str, Dict[str, Any]]:
    """Retrieve per-machine pipeline status from the orchestrator.

    Returns dict mapping machine_id -> {status, last_frame_time, ...}.
    """
    if _pipeline_orchestrator is None:
        return {}
    return _pipeline_orchestrator.get_all_statuses()


@router.get("/health")
async def health_check(response: Response):
    """System health endpoint.

    Returns 200 when healthy, 503 when degraded:
      - DB unreachable → 503
      - >50% of active pipelines in error/failed state → 503

    Response body includes:
      - status: "healthy" | "degraded"
      - database: {reachable, response_time_ms}
      - pipelines: {<machine_id>: {status, last_frame_time}, ...}
    """
    db_info = await _check_db()
    pipeline_statuses = _get_pipeline_statuses()

    # Determine if >50% of pipelines are in error state
    pipelines_degraded = False
    total_pipelines = len(pipeline_statuses)
    if total_pipelines > 0:
        error_count = sum(
            1 for p in pipeline_statuses.values()
            if p.get("status") in ("error", "failed")
        )
        pipelines_degraded = error_count > (total_pipelines / 2)

    # Overall system status
    is_healthy = db_info["reachable"] and not pipelines_degraded
    status = "healthy" if is_healthy else "degraded"

    body = {
        "status": status,
        "database": db_info,
        "pipelines": pipeline_statuses,
    }

    status_code = 200 if is_healthy else 503
    return JSONResponse(content=body, status_code=status_code)


@router.get("/health/ready")
async def readiness_check(response: Response):
    """Readiness probe — returns 200 only when:
      1. Database migrations have been applied (DB is reachable and has _migrations table)
      2. At least one pipeline is currently running

    Used by orchestrators/load-balancers to determine if the app can serve traffic.
    """
    # Check 1: DB reachable and migrations applied
    migrations_ok = False
    if _async_db is not None:
        try:
            row = await _async_db.fetch_one(
                "SELECT COUNT(*) as cnt FROM _migrations"
            )
            # row is an aiosqlite.Row — access by index
            migrations_ok = row is not None and row[0] > 0
        except Exception as exc:
            logger.debug("Readiness: migrations check failed: %s", exc)
            migrations_ok = False

    # Check 2: At least one pipeline running
    pipeline_statuses = _get_pipeline_statuses()
    any_running = any(
        p.get("status") == "running" for p in pipeline_statuses.values()
    )

    ready = migrations_ok and any_running

    body = {
        "ready": ready,
        "migrations_applied": migrations_ok,
        "pipelines_running": any_running,
    }

    status_code = 200 if ready else 503
    return JSONResponse(content=body, status_code=status_code)
