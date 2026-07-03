"""Authentication module for Cologic Shop Floor Tracker.

Provides:
- Password hashing/verification (stdlib only — hashlib + secrets)
- Session token management (stored in SQLite user_sessions table)
- FastAPI router for login/logout/me/change-password
- Helper functions: get_current_user(), require_role()
- Default admin bootstrap on first run (non-fatal)

Session cookie name: sft_session
Session TTL: 8 hours
Role hierarchy: admin(3) > supervisor(2) > viewer(1)
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

SESSION_COOKIE = "sft_session"
SESSION_TTL_HOURS = 8
ROLE_RANK = {"admin": 3, "supervisor": 2, "viewer": 1}

auth_router = APIRouter(prefix="/auth", tags=["auth"])

# Shared DB reference set at startup
_db = None


def set_auth_db(db):
    """Register the DB instance for auth operations."""
    global _db
    _db = db


# ── Password helpers ─────────────────────────────────────────

def _hash_password(password: str) -> str:
    """Return 'salt:hash' string using sha256."""
    salt = secrets.token_hex(8)
    h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored 'salt:hash' string."""
    try:
        salt, expected = stored.split(":", 1)
        h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return secrets.compare_digest(h, expected)
    except Exception:
        return False


# ── Default admin bootstrap ───────────────────────────────────

def create_default_admin(db) -> None:
    """Create cologic/cologic2026 if no users exist. Non-fatal — never raises."""
    try:
        row = db.fetch_one("SELECT COUNT(*) as cnt FROM users")
        if row and row["cnt"] == 0:
            pwd = _hash_password("cologic2026")
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                ("cologic", pwd, "admin"),
            )
            logger.info("Default admin user created (username=cologic, password=cologic2026)")
    except Exception as e:
        logger.warning("Default admin creation failed (non-fatal): %s", e)


# ── Session helpers ───────────────────────────────────────────

def _create_session(db, user_id: int, username: str, role: str) -> str:
    """Insert a new session token and return it."""
    token = secrets.token_hex(32)
    expires = datetime.now() + timedelta(hours=SESSION_TTL_HOURS)
    db.execute(
        """INSERT INTO user_sessions (token, user_id, username, role, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (token, user_id, username, role, expires.isoformat()),
    )
    return token


def _get_session(db, token: str) -> Optional[dict]:
    """Return session dict if valid and not expired, else None."""
    if not token:
        return None
    row = db.fetch_one(
        "SELECT * FROM user_sessions WHERE token = ?", (token,)
    )
    if not row:
        return None
    session = dict(row)
    expires = datetime.fromisoformat(session["expires_at"])
    if datetime.now() > expires:
        # Clean up expired session
        try:
            db.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
        except Exception:
            pass
        return None
    return session


def _delete_session(db, token: str) -> None:
    """Remove a session token from the DB."""
    try:
        db.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
    except Exception:
        pass


# ── Public helpers used by middleware ─────────────────────────

def get_current_user(request: Request) -> Optional[dict]:
    """Extract and validate session from cookie. Returns user dict or None."""
    if _db is None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    return _get_session(_db, token)


def require_role(request: Request, min_role: str = "viewer") -> dict:
    """Raise 401/403 if user isn't authenticated or doesn't meet min_role.

    Returns the user dict on success.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if ROLE_RANK.get(user["role"], 0) < ROLE_RANK.get(min_role, 0):
        raise HTTPException(
            status_code=403,
            detail=f"Requires role '{min_role}' or higher",
        )
    return user


# ── Request/Response models ───────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ── Endpoints ─────────────────────────────────────────────────

@auth_router.post("/login")
async def login(body: LoginRequest, response: Response):
    """POST /auth/login — authenticate and set session cookie."""
    if _db is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    row = _db.fetch_one(
        "SELECT id, username, password_hash, role FROM users WHERE username = ?",
        (body.username,),
    )
    if not row or not _verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user = dict(row)
    token = _create_session(_db, user["id"], user["username"], user["role"])

    # Update last_login
    try:
        _db.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.now().isoformat(), user["id"]),
        )
    except Exception:
        pass

    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_HOURS * 3600,
        path="/",
    )
    logger.info("User '%s' logged in (role=%s)", user["username"], user["role"])
    return {"username": user["username"], "role": user["role"]}


@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    """POST /auth/logout — clear session."""
    token = request.cookies.get(SESSION_COOKIE)
    if token and _db:
        _delete_session(_db, token)
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"status": "logged_out"}


@auth_router.get("/me")
async def me(request: Request):
    """GET /auth/me — return current user info or 401."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": user["username"], "role": user["role"]}


@auth_router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request):
    """POST /auth/change-password — change own password."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    row = _db.fetch_one(
        "SELECT password_hash FROM users WHERE username = ?", (user["username"],)
    )
    if not row or not _verify_password(body.current_password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    new_hash = _hash_password(body.new_password)
    _db.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?",
        (new_hash, user["username"]),
    )
    logger.info("User '%s' changed their password", user["username"])
    return {"status": "password_changed"}


# ── Admin: user management ────────────────────────────────────

@auth_router.get("/users")
async def list_users(request: Request):
    """GET /auth/users — list all users (admin only)."""
    require_role(request, "admin")
    rows = _db.fetch_all(
        "SELECT id, username, role, created_at, last_login FROM users ORDER BY username"
    )
    return [dict(r) for r in rows]


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"


@auth_router.post("/users")
async def create_user(body: CreateUserRequest, request: Request):
    """POST /auth/users — create a new user (admin only)."""
    require_role(request, "admin")
    if body.role not in ROLE_RANK:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {list(ROLE_RANK)}")
    try:
        pwd = _hash_password(body.password)
        _db.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (body.username, pwd, body.role),
        )
        logger.info("Admin created user '%s' (role=%s)", body.username, body.role)
        return {"status": "created", "username": body.username, "role": body.role}
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Username already exists")
        raise HTTPException(status_code=500, detail=str(e))


@auth_router.delete("/users/{username}")
async def delete_user(username: str, request: Request):
    """DELETE /auth/users/{username} — delete a user (admin only, can't delete self)."""
    current = require_role(request, "admin")
    if username == current["username"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    _db.execute("DELETE FROM users WHERE username = ?", (username,))
    # Also clear their sessions
    _db.execute("DELETE FROM user_sessions WHERE username = ?", (username,))
    logger.info("Admin deleted user '%s'", username)
    return {"status": "deleted"}
