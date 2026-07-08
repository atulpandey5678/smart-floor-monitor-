"""Authentication module for Cologic Shop Floor Tracker.

Provides:
- Password hashing/verification (bcrypt, work factor 12)
- Session token management (stored in SQLite user_sessions table)
- FastAPI router for login/logout/me/change-password
- CSRF double-submit cookie pattern for state-changing endpoints
- Login rate limiting (5 failed attempts per 15 minutes)
- Helper functions: await get_current_user(), await require_role()
- Default admin bootstrap on first run (non-fatal)

Session cookie name: sft_session
Session TTL: 8 hours
Role hierarchy: admin(3) > supervisor(2) > viewer(1)
"""

import hashlib
import structlog
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

SESSION_COOKIE = "sft_session"
CSRF_COOKIE = "csrf_token"
SESSION_TTL_HOURS = 8
ROLE_RANK = {"admin": 3, "supervisor": 2, "viewer": 1}

# Password constraints
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# Login rate limiting: 5 failed attempts in 15 minutes
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 15 * 60  # 15 minutes

auth_router = APIRouter(prefix="/auth", tags=["auth"])

# Shared DB reference set at startup
_db = None

# Failed login attempts tracker: {username: [timestamp, ...]}
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def set_auth_db(db):
    """Register the DB instance for auth operations."""
    global _db
    _db = db


# ── Password helpers ─────────────────────────────────────────

def _hash_password(password: str) -> str:
    """Hash password using bcrypt with work factor 12. Returns bcrypt hash string."""
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
    return hashed.decode()


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored hash.

    Supports both:
    - New bcrypt hashes (start with '$2b$')
    - Legacy 'salt:sha256hash' format (for migration)
    """
    try:
        if ":" in stored and not stored.startswith("$2b$"):
            # Legacy SHA-256 format: salt:hash
            salt, expected = stored.split(":", 1)
            h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
            return secrets.compare_digest(h, expected)
        else:
            # bcrypt format
            return bcrypt.checkpw(password.encode(), stored.encode())
    except Exception:
        return False


def _is_legacy_hash(stored: str) -> bool:
    """Check if stored hash is legacy SHA-256 format."""
    return ":" in stored and not stored.startswith("$2b$")


def _validate_password_length(password: str) -> None:
    """Raise HTTPException if password doesn't meet length requirements."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must not exceed {MAX_PASSWORD_LENGTH} characters",
        )


# ── Login rate limiting ───────────────────────────────────────

def _is_account_locked(username: str) -> bool:
    """Check if account is locked due to too many failed attempts."""
    now = time.time()
    cutoff = now - LOCKOUT_WINDOW_SECONDS
    # Prune old entries
    _failed_attempts[username] = [
        t for t in _failed_attempts[username] if t > cutoff
    ]
    return len(_failed_attempts[username]) >= MAX_FAILED_ATTEMPTS


def _record_failed_attempt(username: str) -> None:
    """Record a failed login attempt."""
    _failed_attempts[username].append(time.time())


def _clear_failed_attempts(username: str) -> None:
    """Clear failed attempts on successful login."""
    _failed_attempts.pop(username, None)


# ── CSRF helpers ──────────────────────────────────────────────

def _generate_csrf_token() -> str:
    """Generate a new CSRF token."""
    return secrets.token_hex(32)


def _set_csrf_cookie(response: Response, request: Request) -> str:
    """Set CSRF token cookie and return the token value."""
    token = _generate_csrf_token()
    secure = _should_set_secure(request)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=False,  # Must be readable by JS for double-submit
        secure=secure,
        samesite="strict",
        path="/",
    )
    return token


def verify_csrf_token(request: Request) -> None:
    """Verify CSRF double-submit cookie pattern.

    Checks that X-CSRF-Token header matches csrf_token cookie.
    Should be called on POST/PUT/DELETE requests.
    """
    # Skip CSRF for login (no cookie yet) and logout
    path = request.url.path
    if path.endswith("/login") or path.endswith("/logout"):
        return

    cookie_token = request.cookies.get(CSRF_COOKIE)
    header_token = request.headers.get("X-CSRF-Token")

    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=403,
            detail="CSRF token missing",
        )
    if not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=403,
            detail="CSRF token mismatch",
        )


# ── Cookie helpers ────────────────────────────────────────────

def _should_set_secure(request: Request) -> bool:
    """Determine if Secure flag should be set on cookies.

    Returns False for localhost/127.0.0.1 (development), True otherwise.
    """
    host = request.headers.get("host", "")
    # Strip port if present
    hostname = host.split(":")[0] if ":" in host else host
    return hostname not in ("127.0.0.1", "localhost")


# ── Session helpers ───────────────────────────────────────────

async def _create_session(db, user_id: int, username: str, role: str) -> str:
    """Insert a new session token and return it."""
    token = secrets.token_hex(32)
    expires = datetime.now() + timedelta(hours=SESSION_TTL_HOURS)
    db.execute(
        """INSERT INTO user_sessions (token, user_id, username, role, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (token, user_id, username, role, expires.isoformat()),
    )
    return token


async def _get_session(db, token: str) -> Optional[dict]:
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


async def _delete_session(db, token: str) -> None:
    """Remove a session token from the DB."""
    try:
        db.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
    except Exception:
        pass


# ── Public helpers used by middleware ─────────────────────────

async def get_current_user(request: Request) -> Optional[dict]:
    """Extract and validate session from cookie. Returns user dict or None."""
    if _db is None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    return await _get_session(_db, token)


async def require_role(request: Request, min_role: str = "viewer") -> dict:
    """Raise 401/403 if user isn't authenticated or doesn't meet min_role.

    Returns the user dict on success.
    """
    user = await get_current_user(request)
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
async def login(body: LoginRequest, request: Request, response: Response):
    """POST /auth/login — authenticate and set session cookie."""
    if _db is None:
        raise HTTPException(status_code=503, detail="Service not ready")

    # Check account lockout
    if _is_account_locked(body.username):
        raise HTTPException(
            status_code=429,
            detail="Account temporarily locked due to too many failed attempts. Try again later.",
        )

    # Validate password length
    _validate_password_length(body.password)

    row = _db.fetch_one(
        "SELECT id, username, password_hash, role FROM users WHERE username = ?",
        (body.username,),
    )
    if not row or not _verify_password(body.password, row["password_hash"]):
        if row:
            _record_failed_attempt(body.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user = dict(row)

    # Clear failed attempts on successful login
    _clear_failed_attempts(body.username)

    # Migrate legacy hash to bcrypt on successful login
    if _is_legacy_hash(user["password_hash"]):
        new_hash = _hash_password(body.password)
        try:
            _db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_hash, user["id"]),
            )
            logger.info("Migrated password hash to bcrypt for user '%s'", user["username"])
        except Exception:
            pass  # Non-fatal: will migrate next login

    token = await _create_session(_db, user["id"], user["username"], user["role"])

    # Update last_login
    try:
        _db.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (datetime.now().isoformat(), user["id"]),
        )
    except Exception:
        pass

    secure = _should_set_secure(request)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=SESSION_TTL_HOURS * 3600,
        path="/",
    )

    # Set CSRF cookie for double-submit pattern
    _set_csrf_cookie(response, request)

    logger.info("User '%s' logged in (role=%s)", user["username"], user["role"])
    return {"username": user["username"], "role": user["role"]}


@auth_router.post("/logout")
async def logout(request: Request, response: Response):
    """POST /auth/logout — clear session."""
    token = request.cookies.get(SESSION_COOKIE)
    if token and _db:
        await _delete_session(_db, token)
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    response.delete_cookie(key=CSRF_COOKIE, path="/")
    return {"status": "logged_out"}


@auth_router.get("/me")
async def me(request: Request):
    """GET /auth/me — return current user info or 401."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"username": user["username"], "role": user["role"]}


@auth_router.post("/change-password")
async def change_password(body: ChangePasswordRequest, request: Request):
    """POST /auth/change-password — change own password."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # CSRF check for state-changing endpoint
    verify_csrf_token(request)

    # Validate new password length
    _validate_password_length(body.new_password)

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
    await require_role(request, "admin")
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
    await require_role(request, "admin")

    # CSRF check for state-changing endpoint
    verify_csrf_token(request)

    if body.role not in ROLE_RANK:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {list(ROLE_RANK)}")

    # Validate password length
    _validate_password_length(body.password)

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
    current = await require_role(request, "admin")

    # CSRF check for state-changing endpoint
    verify_csrf_token(request)

    if username == current["username"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    _db.execute("DELETE FROM users WHERE username = ?", (username,))
    # Also clear their sessions
    _db.execute("DELETE FROM user_sessions WHERE username = ?", (username,))
    logger.info("Admin deleted user '%s'", username)
    return {"status": "deleted"}
