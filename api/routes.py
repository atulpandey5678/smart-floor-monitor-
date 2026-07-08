"""REST API routes for Shop Floor Tracker."""

from datetime import date, datetime, timedelta
from dataclasses import asdict
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from api.schemas import (
    AlertResolve,
    CameraZonePayload,
    ChatRequest,
    EmployeeCreate,
    SettingsUpdate,
)

router = APIRouter()


# ── Shared State (set from server.py) ────────────────────────
# These are populated by the application during startup.
_current_state = {}
_repo = None


def set_repo(repo):
    """Set the repository instance for route handlers."""
    global _repo
    _repo = repo


def set_state(state: dict):
    """Update the current live state dict."""
    global _current_state
    _current_state = state


def get_state():
    """Return the current live state dict."""
    return _current_state


# Shared LightDetector reference (set from main.py via set_light_detector)
_light_detector = None


def set_light_detector(detector):
    """Register the live LightDetector so zone updates from the wizard apply."""
    global _light_detector
    _light_detector = detector


# ── Endpoints ────────────────────────────────────────────────


@router.get("/status")
async def get_status():
    """GET /api/status — current live state including active session info, detection indicators, and efficiency."""
    state = _current_state or {
        "state": "IDLE",
        "badge_id": None,
        "employee_name": None,
        "active_duration_seconds": 0.0,
        "body_detected": False,
        "badge_detected": False,
        "efficiency_percent": 0.0,
    }
    return state




@router.get("/sessions")
async def get_sessions(
    date: Optional[str] = Query(None),
    machine_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """GET /api/sessions?date=YYYY-MM-DD&machine_id=M-01&page=1&page_size=20"""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    from api.pagination import PaginationParams, paginated_response
    pagination = PaginationParams(page, page_size)

    if date:
        try:
            parsed = datetime.strptime(date, "%Y-%m-%d").date()
            return await _repo.get_sessions_for_date(parsed)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date")

    items = await _repo.get_today_sessions(
        machine_id=machine_id, limit=pagination.limit, offset=pagination.offset
    )
    total = await _repo.count_today_sessions(machine_id=machine_id)
    return paginated_response(items, total, pagination.page, pagination.page_size)


@router.get("/sessions/today")
async def get_sessions_today(
    machine_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """GET /api/sessions/today — sessions for the current day with optional machine_id filter."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    from api.pagination import PaginationParams, paginated_response
    params = PaginationParams(page, page_size)
    items = await _repo.get_today_sessions(machine_id=machine_id, limit=params.limit, offset=params.offset)
    total = await _repo.count_today_sessions(machine_id=machine_id)
    return paginated_response(items, total, params.page, params.page_size)
    

@router.get("/sessions/history")
async def get_sessions_history(
    machine_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """GET /api/sessions/history — sessions from the last 7 days with optional machine_id filter."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    from api.pagination import PaginationParams, paginated_response
    params = PaginationParams(page, page_size)
    items = await _repo.get_history_sessions(days=7, machine_id=machine_id, limit=params.limit, offset=params.offset)
    total = await _repo.count_history_sessions(days=7, machine_id=machine_id)
    return paginated_response(items, total, params.page, params.page_size)


@router.get("/alerts")
async def get_alerts(
    machine_id: Optional[str] = Query(None),
    resolved: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """GET /api/alerts — alerts with optional machine_id and resolved filters (paginated)."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    from api.pagination import PaginationParams, paginated_response
    params = PaginationParams(page, page_size)
    items = await _repo.get_unresolved_alerts(
        machine_id=machine_id, resolved=resolved, limit=params.limit, offset=params.offset
    )
    total = await _repo.count_unresolved_alerts(machine_id=machine_id, resolved=resolved)
    return paginated_response(items, total, params.page, params.page_size)


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int, payload: Optional[AlertResolve] = None):
    """POST /api/alerts/{id}/resolve — mark alert as resolved."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    root_cause = payload.note if payload else None
    resolved = await _repo.resolve_alert(alert_id, root_cause)
    if not resolved:
        raise HTTPException(status_code=404, detail="Alert not found or already resolved")
    return {"status": "resolved", "alert_id": alert_id}


@router.get("/employees")
async def get_employees(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """GET /api/employees — all registered employees (paginated)."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    from api.pagination import PaginationParams, paginated_response
    params = PaginationParams(page, page_size)
    items = await _repo.get_all_employees(limit=params.limit, offset=params.offset)
    total = await _repo.count_employees()
    return paginated_response(items, total, params.page, params.page_size)


@router.post("/employees")
async def create_employee(employee: EmployeeCreate):
    """POST /api/employees — create or update employee record.

    Badge ID must be 4-6 numeric digits (validated by Pydantic model).
    """
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    result = await _repo.upsert_employee(employee.badge_id, employee.name)
    return result


# ── Report Endpoints ───────────────────────────────────────────


@router.get("/reports/daily")
async def get_daily_report(
    date: Optional[str] = Query(None, alias="date"),
    format: str = Query("json"),
):
    """GET /api/reports/daily?date=YYYY-MM-DD&format=json|csv"""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Validate format
    if format not in ("json", "csv"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{format}'. Supported: json, csv",
        )

    # Parse date (default: today)
    if date:
        try:
            report_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
            )
    else:
        report_date = datetime.now().date()

    from engine.report_engine import ReportEngine
    from config import SHIFT_HOURS

    engine = ReportEngine(_repo, shift_hours=SHIFT_HOURS)
    report = await engine.daily_report(report_date)

    if format == "csv":
        csv_content = engine._format_csv(report)
        filename = f"daily_report_{report.report_date}.csv"
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return asdict(report)


@router.get("/reports/weekly")
async def get_weekly_report(
    week_start: Optional[str] = Query(None),
    format: str = Query("json"),
):
    """GET /api/reports/weekly?week_start=YYYY-MM-DD&format=json|csv"""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Validate format
    if format not in ("json", "csv"):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{format}'. Supported: json, csv",
        )

    # Parse week_start (default: most recent Monday)
    if week_start:
        try:
            start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="Invalid date format. Use YYYY-MM-DD."
            )
    else:
        today = datetime.now().date()
        # Most recent Monday (weekday 0 = Monday)
        start_date = today - timedelta(days=today.weekday())

    from engine.report_engine import ReportEngine
    from config import SHIFT_HOURS

    engine = ReportEngine(_repo, shift_hours=SHIFT_HOURS)
    report = await engine.weekly_report(start_date)

    if format == "csv":
        csv_content = engine._format_csv(report)
        filename = f"weekly_report_{report.week_start}_to_{report.week_end}.csv"
        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return asdict(report)


# ── Machine State Events ──────────────────────────────────────


@router.get("/machine-state-events")
async def get_machine_state_events(
    machine_id: str = Query("M-01"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    """GET /api/machine-state-events — machine light state transition history."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    parsed_from = None
    parsed_to = None

    if date_from:
        try:
            parsed_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format. Use YYYY-MM-DD.")

    if date_to:
        try:
            parsed_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format. Use YYYY-MM-DD.")

    return await _repo.get_machine_state_events(machine_id, date_from=parsed_from, date_to=parsed_to)


# ── Video Feed (single JPEG frame) ────────────────────────────
_get_frame_fn = None


def set_frame_provider(fn):
    """Set the function that returns annotated frame bytes."""
    global _get_frame_fn
    _get_frame_fn = fn


# ── Camera Zones (machine configuration from dashboard) ───────


@router.post("/camera/zones")
async def save_camera_zones(data: CameraZonePayload):
    """POST /api/camera/zones — Save machine zone configuration from the wizard.

    Persists the machine config server-side and updates the live LightDetector zone.
    """
    from engine.settings_manager import get_settings
    import structlog as _log

    # Persist machine config server-side so it survives browser/localStorage wipes
    machine_id = data.machine_id
    if machine_id:
        try:
            settings = get_settings()
            machines = settings.get("machines", "list", [])
            if not isinstance(machines, list):
                machines = []
            # Replace existing entry or append
            existing = next((i for i, m in enumerate(machines)
                             if m.get("id") == machine_id or m.get("machineName") == machine_id
                             or m.get("name") == machine_id), None)
            # Convert to dict, including extra fields from wizard
            entry = data.model_dump(exclude_none=True)
            entry["id"] = machine_id
            entry["machine_id"] = machine_id
            if existing is not None:
                machines[existing] = entry
            else:
                machines.append(entry)
            await settings.set("machines", "list", machines)
            _log.getLogger(__name__).info("Machine config saved: %s (%d total)", machine_id, len(machines))
        except Exception as ex:
            _log.getLogger(__name__).warning("Failed to persist machine config: %s", ex)

    # Update the live LightDetector zone if a lightZone is included
    if data.lightZone:
        try:
            zone_tuple = (
                data.lightZone.x1,
                data.lightZone.y1,
                data.lightZone.x2,
                data.lightZone.y2,
            )
            if _light_detector:
                _light_detector.set_zone(zone_tuple)
                _log.getLogger(__name__).info(
                    "Light zone updated from wizard: %s", zone_tuple)
        except (KeyError, ValueError, TypeError):
            pass

    return {"status": "saved"}


@router.get("/video_feed")
async def video_feed():
    """GET /api/video_feed — Returns current JPEG frame with detection overlays."""
    from fastapi.responses import Response
    import numpy as np
    import cv2

    if _get_frame_fn:
        frame_bytes = _get_frame_fn()
        if frame_bytes:
            return Response(
                content=frame_bytes,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
            )

    # Return a dark placeholder with "Connecting…" text
    placeholder = np.zeros((360, 640, 3), dtype=np.uint8)
    placeholder[:] = (18, 27, 42)  # dark navy
    cv2.putText(placeholder, "No camera configured", (180, 175),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 2)
    cv2.putText(placeholder, "Add a camera via Camera Setup", (155, 210),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 80), 1)
    _, jpeg = cv2.imencode('.jpg', placeholder)
    return Response(
        content=jpeg.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@router.get("/stream")
async def video_stream():
    """GET /api/stream — MJPEG video stream at up to 25 FPS."""
    import asyncio
    from fastapi.responses import StreamingResponse

    async def frame_generator():
        last_frame = None
        while True:
            if _get_frame_fn:
                frame_bytes = _get_frame_fn()
                # Only push a new frame if it changed (avoids duplicate sends)
                if frame_bytes and frame_bytes is not last_frame:
                    last_frame = frame_bytes
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            await asyncio.sleep(0.033)  # Max ~30 FPS

    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")


# ── Settings API (admin only) ─────────────────────────────────

VALID_SECTIONS = {"detection", "light", "shifts", "notifications", "branding", "retention", "system"}


@router.get("/settings/{section}")
async def get_settings_section(section: str, request: "Request"):
    """GET /api/settings/{section} — Return settings for a section (admin only)."""
    from api.auth import require_role
    from engine.settings_manager import get_settings

    if section not in VALID_SECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown settings section '{section}'")

    await require_role(request, "admin")
    settings = get_settings()
    return settings.section(section)


@router.put("/settings/{section}")
async def update_settings_section(section: str, data: SettingsUpdate, request: "Request"):
    """PUT /api/settings/{section} — Update settings for a section (admin only)."""
    from api.auth import require_role
    from engine.settings_manager import get_settings

    if section not in VALID_SECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown settings section '{section}'")

    # Admin-only
    await require_role(request, "admin")

    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    settings = get_settings()
    # Convert to dict for storage, excluding unset fields
    settings_data = data.model_dump(exclude_unset=True)
    await settings.set_section(section, settings_data)

    import structlog
    structlog.get_logger(__name__).info("Settings updated", section=section, keys=list(settings_data.keys()))
    return {"status": "saved", "section": section}


@router.get("/settings")
async def get_all_settings(request: "Request"):
    """GET /api/settings — Return all settings sections (admin only)."""
    from api.auth import require_role
    from engine.settings_manager import get_settings, DEFAULTS

    await require_role(request, "admin")
    settings = get_settings()
    return {section: settings.section(section) for section in VALID_SECTIONS}


# ── Backup / Restore ──────────────────────────────────────────

@router.get("/settings/backup/download")
async def download_backup(request: "Request"):
    """GET /api/settings/backup/download — stream the SQLite DB as a file download (admin only)."""
    import os
    import shutil
    import tempfile
    from fastapi.responses import FileResponse
    from api.auth import require_role

    await require_role(request, "admin")

    db_path = DB_PATH
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Database file not found")

    # Copy to a temp file so the live DB isn't locked during download
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.close()
    shutil.copy2(db_path, tmp.name)

    from datetime import datetime as _dt
    filename = f"cologic_backup_{_dt.now().strftime('%Y%m%d_%H%M%S')}.db"
    return FileResponse(
        path=tmp.name,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=None,
    )


@router.post("/settings/backup/restore")
async def restore_backup(request: "Request"):
    """POST /api/settings/backup/restore — replace the DB with an uploaded backup (admin only)."""
    import os
    import shutil
    import sqlite3
    import tempfile
    from fastapi import UploadFile, File
    from api.auth import require_role

    await require_role(request, "admin")

    # Parse multipart body manually since we can't use UploadFile in the type hint here
    form = await request.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(status_code=400, detail="No file uploaded — send field named 'file'")

    contents = await upload.read()
    if len(contents) < 16:
        raise HTTPException(status_code=400, detail="File too small to be a valid SQLite database")

    # Validate it's actually a SQLite file
    if not contents.startswith(b"SQLite format 3"):
        raise HTTPException(status_code=400, detail="Uploaded file does not appear to be a valid SQLite database")

    # Write to a temp file, verify it opens cleanly
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
    tmp.write(contents)
    tmp.close()
    try:
        conn = sqlite3.connect(tmp.name)
        conn.execute("PRAGMA integrity_check")
        conn.close()
    except Exception as e:
        os.unlink(tmp.name)
        raise HTTPException(status_code=400, detail=f"Database integrity check failed: {e}")

    # Make a safety backup of the current DB before replacing
    db_path = DB_PATH
    if os.path.exists(db_path):
        from datetime import datetime as _dt
        safety = db_path + f".pre_restore_{_dt.now().strftime('%Y%m%d_%H%M%S')}.bak"
        shutil.copy2(db_path, safety)

    # Replace the live DB
    shutil.move(tmp.name, db_path)

    import structlog
    structlog.get_logger(__name__).warning("Database restored from backup upload by admin")
    return {"status": "restored", "message": "Database replaced. Restart the server for full effect."}


# ── Employee delete ───────────────────────────────────────────

@router.delete("/employees/{employee_id}")
async def delete_employee(employee_id: str, request: "Request"):
    """DELETE /api/employees/{id} — remove an employee record (admin only)."""
    from api.auth import require_role
    await require_role(request, "admin")
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    await _repo.db.execute("DELETE FROM employees WHERE badge_id = ?", (employee_id,))
    return {"status": "deleted", "badge_id": employee_id}


# ── First-Run Setup (disabled — fixed credentials seeded on startup) ──────

@router.get("/setup/status")
async def setup_status():
    """GET /api/setup/status — always returns setup_complete: true (fixed credentials)."""
    return {"setup_complete": True}


# ── Alert history (for bell/alert center) ────────────────────

@router.get("/alerts/history")
async def get_alerts_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """GET /api/alerts/history — all alerts (resolved + unresolved) for alert center."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return await _repo.get_alerts_history(limit=limit, offset=offset)


@router.get("/alerts/unread-count")
async def get_alerts_unread_count():
    """GET /api/alerts/unread-count — count of unresolved alerts."""
    if _repo is None:
        return {"count": 0}
    row = await _repo.db.fetch_one("SELECT COUNT(*) as cnt FROM alerts WHERE resolved = 0")
    return {"count": row["cnt"] if row else 0}


# ── AI Chat ──────────────────────────────────────────────────

@router.post("/ai/chat")
async def chat_with_ai(request: ChatRequest):
    """POST /api/ai/chat — Converse with Claude, powered by Anthropic Tool Calling."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    
    try:
        from engine.ai_chat import handle_chat_message
        # Convert validated ChatMessage objects back to dicts for the engine
        messages = [msg.model_dump() for msg in request.messages]
        reply = await handle_chat_message(messages, _repo)
        return {"reply": reply}
    except Exception as e:
        import structlog
        structlog.get_logger(__name__).error("Chat endpoint error", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to process chat message")


# ── Machine Config persistence (server-side) ──────────────────
# Machines are stored in app_settings as section="machines", key=machine_id

@router.get("/machines")
async def get_machines(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """GET /api/machines — return all saved machine configurations (paginated)."""
    from engine.settings_manager import get_settings
    from api.pagination import PaginationParams, paginated_response
    settings = get_settings()
    machines_data = settings.get("machines", "list", [])
    params = PaginationParams(page, page_size)
    total = len(machines_data)
    paginated_items = machines_data[params.offset : params.offset + params.limit]
    return paginated_response(paginated_items, total, params.page, params.page_size)


@router.delete("/machines/{machine_id}")
async def delete_machine(machine_id: str, request: Request):
    """DELETE /api/machines/{machine_id} — remove a machine (admin only)."""
    from api.auth import require_role
    from engine.settings_manager import get_settings
    await require_role(request, "admin")
    settings = get_settings()
    machines = settings.get("machines", "list", [])
    machines = [m for m in machines if m.get("id") != machine_id and m.get("name") != machine_id]
    await settings.set("machines", "list", machines)
    return {"status": "deleted", "machine_id": machine_id}


# ── Backup download ───────────────────────────────────────────

@router.get("/settings/backup/download")
async def download_backup(request: Request):
    """GET /api/settings/backup/download — download a copy of the SQLite DB."""
    from api.auth import require_role
    from fastapi.responses import FileResponse
    import shutil, tempfile
    require_role(request, "admin")
    from config import DB_PATH
    # Copy DB to a temp file so the response can safely read it
    tmp = tempfile.mktemp(suffix=".db")
    try:
        shutil.copy2(DB_PATH, tmp)
        return FileResponse(
            path=tmp,
            media_type="application/octet-stream",
            filename="cologic_backup.db",
        )
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Backup failed: {ex}")
