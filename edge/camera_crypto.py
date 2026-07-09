"""Camera credential crypto/redaction helpers — Edge_Agent copy.

RTSP URLs and camera credentials live **only** on the Edge_Agent
(Requirements 7.6, 13.1). These helpers — Fernet encryption/decryption of RTSP
URLs at rest, credential redaction for safe logging, and RTSP URL validation —
are the edge-side relocation of the equivalent helpers that previously lived in
``engine/machine_registry.py``.

They are kept as a dedicated edge module (rather than imported from the cloud
``machine_registry``) so the credential-handling code physically resides with
the component that owns the secrets and so the cloud registry can drop RTSP
handling entirely without breaking the edge. The originals remain in
``engine/machine_registry.py`` for the authoritative (on-prem) MachineRegistry.

Requirements: 7.6, 13.1
"""

from __future__ import annotations

import base64
import hashlib
import os
from urllib.parse import urlparse, urlunparse

import structlog

logger = structlog.get_logger(__name__)

# Base64-encoded 32-byte Fernet key. Read from the environment so the secret
# never lives in source. Generate with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
_FERNET_KEY = os.getenv("FERNET_KEY")


def _get_fernet():
    """Return a Fernet instance keyed from the ``FERNET_KEY`` environment var.

    Falls back to a deterministic development-only key when ``FERNET_KEY`` is
    unset. The fallback is NOT secure and must not be used in production.
    """
    from cryptography.fernet import Fernet

    key = _FERNET_KEY
    if not key:
        seed = b"cologic-dev-only-insecure-key-seed"
        raw = hashlib.sha256(seed).digest()
        key = base64.urlsafe_b64encode(raw).decode()
        logger.warning(
            "FERNET_KEY not set — using insecure dev fallback. "
            "Set FERNET_KEY for production."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_rtsp_url(url: str) -> str:
    """Encrypt an RTSP URL for at-rest storage in Local_Camera_Config."""
    return _get_fernet().encrypt(url.encode()).decode()


def decrypt_rtsp_url(encrypted: str) -> str:
    """Decrypt an RTSP URL previously produced by :func:`encrypt_rtsp_url`."""
    return _get_fernet().decrypt(encrypted.encode()).decode()


def redact_rtsp_url(url: str) -> str:
    """Strip credentials from an RTSP URL for safe display/logging.

    Example: ``rtsp://admin:pass@host:554/path`` -> ``rtsp://***:***@host:554/path``.
    Never raises — returns a fully-masked placeholder on any parse failure.
    """
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = f"***:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "rtsp://***"


def validate_rtsp_url(url: str) -> bool:
    """Return True if ``url`` is a well-formed ``rtsp``/``rtsps`` URI."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("rtsp", "rtsps") and bool(parsed.hostname)
    except Exception:
        return False
