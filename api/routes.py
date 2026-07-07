"""REST API routes for Shop Floor Tracker."""

import re
import time
from datetime import date, datetime, timedelta
from dataclasses import asdict
from typing import Optional, Any
from pydantic import BaseModel, field_validator
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

router = APIRouter(prefix="/api")

# ── Request/Response Models ──────────────────────────────────


class EmployeeCreate(BaseModel):
    badge_id: str
    name: str

    @field_validator('badge_id')
    @classmethod
    def validate_badge_id(cls, v):
        if not re.match(r'^\d{4,6}$', v):
            raise ValueError('Badge ID must be 4-6 numeric digits')
        return v


class AlertResolve(BaseModel):
    note: Optional[str] = None


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
async def get_sessions(date: Optional[str] = Query(None)):
    """GET /api/sessions?date=YYYY-MM-DD"""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    if date:
        try:
            parsed = datetime.strptime(date, "%Y-%m-%d").date()
            return await _repo.get_sessions_for_date(parsed)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date")
    return await _repo.get_today_sessions()


@router.get("/sessions/today")
async def get_sessions_today():
    """GET /api/sessions/today — all sessions for the current day."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return await _repo.get_today_sessions()
    

@router.get("/sessions/history")
async def get_sessions_history():
    """GET /api/sessions/history — sessions from the last 7 days."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return await _repo.get_history_sessions(days=7)


@router.get("/alerts")
async def get_alerts():
    """GET /api/alerts — all unresolved alerts."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return await _repo.get_unresolved_alerts()


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
async def get_employees():
    """GET /api/employees — all registered employees."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return await _repo.get_all_employees()


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
async def save_camera_zones(data: dict):
    """POST /api/camera/zones — Save machine zone configuration from the wizard.

    If the body includes a 'lightZone' field, updates the live LightDetector zone.
    """
    light_zone = data.get('lightZone')
    if light_zone and isinstance(light_zone, dict):
        try:
            zone_tuple = (
                float(light_zone['x1']),
                float(light_zone['y1']),
                float(light_zone['x2']),
                float(light_zone['y2']),
            )
            if _light_detector:
                _light_detector.set_zone(zone_tuple)
                import logging
                logging.getLogger(__name__).info("Light zone updated from wizard: %s", zone_tuple)
        except (KeyError, ValueError, TypeError):
            pass  # Ignore malformed lightZone data

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
    cv2.putText(placeholder, "Connecting to camera...", (160, 175),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (150, 150, 150), 2)
    cv2.putText(placeholder, "rtsp://192.168.0.36", (210, 210),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 80), 1)
    _, jpeg = cv2.imencode('.jpg', placeholder)
    return Response(
        content=jpeg.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


# ── Settings API (admin only) ─────────────────────────────────

VALID_SECTIONS = {"detection", "light", "shifts", "notifications", "branding", "retention"}


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
async def update_settings_section(section: str, data: dict, request: "Request"):
    """PUT /api/settings/{section} — Update settings for a section (admin only)."""
    from fastapi import Request
    from api.auth import require_role
    from engine.settings_manager import get_settings

    if section not in VALID_SECTIONS:
        raise HTTPException(status_code=404, detail=f"Unknown settings section '{section}'")

    # Admin-only
    await require_role(request, "admin")

    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    settings = get_settings()
    await settings.set_section(section, data)

    import logging
    logging.getLogger(__name__).info("Settings updated: section=%s keys=%s", section, list(data.keys()))
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

    import logging
    logging.getLogger(__name__).warning("Database restored from backup upload by admin")
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


# ── First-Run Setup ───────────────────────────────────────────

@router.get("/setup/status")
async def setup_status():
    """GET /api/setup/status — returns whether initial setup has been completed.

    Returns {setup_complete: bool}. Auth-exempt so setup.html can call it freely.
    """
    if _repo is None:
        return {"setup_complete": False}
    try:
        row = await _repo.db.fetch_one("SELECT COUNT(*) as cnt FROM users")
        user_count = row["cnt"] if row else 0
        return {"setup_complete": user_count > 0}
    except Exception:
        return {"setup_complete": False}


class SetupInitRequest(BaseModel):
    username: str
    password: str
    company_name: str = "Cologic"
    logo_url: str = ""
    primary_color: str = "#6366F1"


@router.post("/setup/init")
async def setup_init(body: SetupInitRequest):
    """POST /api/setup/init — create the first admin account and branding.

    Only succeeds if NO users exist yet (prevents privilege escalation).
    Auth-exempt so it works before any user is created.
    """
    from api.auth import _hash_password, _create_session
    from engine.settings_manager import get_settings

    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Safety check: abort if any user already exists
    try:
        row = await _repo.db.fetch_one("SELECT COUNT(*) as cnt FROM users")
        if row and row["cnt"] > 0:
            raise HTTPException(
                status_code=409,
                detail="Setup already completed — admin account exists"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Validate inputs
    if not body.username.strip():
        raise HTTPException(status_code=400, detail="Username cannot be empty")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    # Create the first admin user
    pwd_hash = _hash_password(body.password)
    try:
        await _repo.db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (body.username.strip(), pwd_hash, "admin"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {e}")

    # Save branding settings
    try:
        settings = get_settings()
        await settings.set("branding", "company_name", body.company_name or "Cologic")
        if body.logo_url:
            await settings.set("branding", "logo_url", body.logo_url)
        if body.primary_color:
            await settings.set("branding", "primary_color", body.primary_color)
    except Exception:
        pass  # Branding save is non-fatal

    import logging
    logging.getLogger(__name__).info(
        "First-run setup completed. Admin user '%s' created.", body.username
    )
    return {"status": "setup_complete", "username": body.username}


# ── Alert history (for bell/alert center) ────────────────────

@router.get("/alerts/history")
async def get_alerts_history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """GET /api/alerts/history — all alerts (resolved + unresolved) for alert center."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    rows = await _repo.db.fetch_all(
        """SELECT id, badge_id, alert_type, message, resolved, root_cause, created_at
           FROM alerts
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    )
    return [dict(r) for r in rows]


@router.get("/alerts/unread-count")
async def get_alerts_unread_count():
    """GET /api/alerts/unread-count — count of unresolved alerts."""
    if _repo is None:
        return {"count": 0}
    row = await _repo.db.fetch_one("SELECT COUNT(*) as cnt FROM alerts WHERE resolved = 0")
    return {"count": row["cnt"] if row else 0}


# ── AI Chat ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    messages: list[dict[str, Any]]

@router.post("/ai/chat")
async def chat_with_ai(request: ChatRequest):
    """POST /api/ai/chat — Converse with Claude, powered by Anthropic Tool Calling."""
    if _repo is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    
    try:
        from engine.ai_chat import handle_chat_message
        reply = await handle_chat_message(request.messages, _repo)
        return {"reply": reply}
    except Exception as e:
        import logging
        logging.getLogger(__name__).error("Chat endpoint error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to process chat message")
