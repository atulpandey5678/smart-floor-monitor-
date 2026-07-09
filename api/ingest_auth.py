"""API-key authentication for the Ingest_API (`/api/ingest/*`).

This authentication path is deliberately separate from the Staff_User
cookie login in ``api/auth.py``. The Edge_Agent presents the long-lived
``Ingest_API_Key`` on every ingest request, either as an ``X-Ingest-Key``
header or as an ``Authorization: Bearer <key>`` header. The presented key is
compared to the configured key with :func:`secrets.compare_digest` for a
constant-time comparison that avoids leaking timing information.

Requirements covered:
- 3.4: Validate the Ingest_API_Key on all ``/api/ingest/`` endpoints using a
  mechanism separate from the Staff_User cookie-based login.
- 3.5: Reject Staff_User session cookies as authentication for ingest
  endpoints. This dependency ignores cookies entirely, so a request carrying
  only a staff cookie (and no valid key) is rejected with HTTP 401.
- 3.6: Reject the Ingest_API_Key as authentication for staff dashboard
  endpoints. Staff endpoints authenticate via the session cookie
  (``get_current_user``), which never inspects the ingest headers, so an
  ingest key alone can never satisfy staff auth. See
  :func:`reject_ingest_key_for_staff` for an explicit guard.

The ``/api/ingest/`` prefix is added to ``_AUTH_EXEMPT_PREFIXES`` in
``api/server.py`` so the cookie ``AuthMiddleware`` does not attempt cookie
authentication on ingest routes, and the CSRF middleware skips them (machine
clients cannot hold a CSRF double-submit cookie). Ingest routes are instead
gated by :func:`verify_ingest_key` as a FastAPI dependency.
"""

import secrets
from typing import Optional

import structlog
from fastapi import HTTPException, Request

from config import INGEST_API_KEY

logger = structlog.get_logger(__name__)

# Header the Edge_Agent uses to carry the Ingest_API_Key.
INGEST_KEY_HEADER = "X-Ingest-Key"
_BEARER_PREFIX = "Bearer "

# Path prefix guarded by this dependency. Kept here so server.py and any
# staff-side guard can reference a single source of truth.
INGEST_PATH_PREFIX = "/api/ingest/"


def _extract_key(request: Request) -> Optional[str]:
    """Return the presented ingest key from the request headers, or ``None``.

    Prefers the dedicated ``X-Ingest-Key`` header; falls back to an
    ``Authorization: Bearer <key>`` header.
    """
    key = request.headers.get(INGEST_KEY_HEADER)
    if key:
        return key.strip()

    authorization = request.headers.get("Authorization", "")
    if authorization.startswith(_BEARER_PREFIX):
        candidate = authorization[len(_BEARER_PREFIX):].strip()
        if candidate:
            return candidate

    return None


def verify_ingest_key(request: Request) -> None:
    """FastAPI dependency that authenticates an ingest request by API key.

    Raises HTTP 401 when the configured key is unset, when no key is
    presented, or when the presented key does not match the configured key.
    Cookies are intentionally ignored so staff session cookies cannot be used
    to authenticate ingest requests (Requirement 3.5).
    """
    configured = INGEST_API_KEY
    if not configured:
        # Fail closed: never allow ingest when no key is configured.
        logger.warning("Ingest request rejected: no INGEST_API_KEY configured")
        raise HTTPException(status_code=401, detail="Ingest authentication is not configured")

    presented = _extract_key(request)
    if not presented or not secrets.compare_digest(presented, configured):
        raise HTTPException(status_code=401, detail="Invalid or missing ingest API key")


def has_valid_ingest_key(request: Request) -> bool:
    """Return ``True`` if the request carries a valid ingest key.

    Non-raising helper used by staff-side guards to detect (and reject) the
    ingest key being presented to a staff endpoint (Requirement 3.6).
    """
    configured = INGEST_API_KEY
    if not configured:
        return False
    presented = _extract_key(request)
    return bool(presented) and secrets.compare_digest(presented, configured)


def reject_ingest_key_for_staff(request: Request) -> None:
    """Guard for staff endpoints: reject the Ingest_API_Key as staff auth.

    Staff endpoints authenticate with the session cookie, which never reads
    the ingest headers, so the ingest key can never *grant* staff access. This
    guard makes Requirement 3.6 explicit by refusing any staff request that
    attempts to authenticate with the ingest key.
    """
    if has_valid_ingest_key(request):
        raise HTTPException(
            status_code=401,
            detail="Ingest API key is not valid for staff endpoints",
        )
