"""Machine Registry — CRUD operations for multi-machine station configurations.

Manages machine station registration, validation, and persistence.
RTSP credentials are encrypted at rest using Fernet symmetric encryption.

Requirements: 1.1, 1.2, 1.4, 8.2, 8.3, 8.4
"""

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import structlog
from cryptography.fernet import Fernet

from api.ingest_schemas import MachineMetadata
from db.async_database import AsyncDatabase

logger = structlog.get_logger(__name__)

# ── Credential-free metadata columns (Cloud_Server) ────────────
# The cloud MachineRegistry read path selects ONLY these columns. The
# ``rtsp_url_encrypted`` column is deliberately excluded so RTSP URLs and camera
# credentials are never read into, returned by, or logged from the cloud
# (Requirements 13.2, 13.3). Column order matches ``MachineMetadata`` fields.
_METADATA_COLUMNS = (
    "machine_id",
    "display_name",
    "detection_zone",
    "person_confidence_threshold",
    "light_zone",
    "updated_at",
)
_METADATA_SELECT = ", ".join(_METADATA_COLUMNS)

# ── Encryption Key ─────────────────────────────────────────────
# Must be set via FERNET_KEY environment variable (base64-encoded 32-byte key).
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
_FERNET_KEY = os.getenv("FERNET_KEY")


def _get_fernet() -> Fernet:
    """Return a Fernet instance using the environment key.

    Falls back to a deterministic key derived from a fixed seed if FERNET_KEY
    is not set (development only — not secure for production).
    """
    key = _FERNET_KEY
    if not key:
        # Development fallback — NOT for production use
        import base64
        import hashlib
        seed = b"cologic-dev-only-insecure-key-seed"
        raw = hashlib.sha256(seed).digest()
        key = base64.urlsafe_b64encode(raw).decode()
        logger.warning("FERNET_KEY not set — using insecure dev fallback. Set FERNET_KEY for production.")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_rtsp_url(url: str) -> str:
    """Encrypt an RTSP URL for storage."""
    f = _get_fernet()
    return f.encrypt(url.encode()).decode()


def decrypt_rtsp_url(encrypted: str) -> str:
    """Decrypt an RTSP URL from storage."""
    f = _get_fernet()
    return f.decrypt(encrypted.encode()).decode()


def redact_rtsp_url(url: str) -> str:
    """Strip credentials from an RTSP URL for safe display.

    Example: rtsp://admin:pass@host:554/path -> rtsp://***:***@host:554/path
    """
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            # Reconstruct without credentials
            netloc = f"***:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "rtsp://***"


def validate_rtsp_url(url: str) -> bool:
    """Validate that a URL is a well-formed RTSP URI."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("rtsp", "rtsps") and bool(parsed.hostname)
    except Exception:
        return False


def validate_machine_id(machine_id: str) -> bool:
    """Validate machine_id: alphanumeric + hyphens, 1-20 chars."""
    return bool(re.match(r'^[a-zA-Z0-9\-]{1,20}$', machine_id))


class MachineRegistry:
    """CRUD operations for machine station configurations.

    Uses AsyncDatabase for all persistence. Supports up to 8+ concurrent machines.
    """

    def __init__(self, db: AsyncDatabase):
        self._db = db

    async def register(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new machine station.

        Args:
            config: Dict with machine_id, display_name, rtsp_url, and optional
                    detection_zone, person_confidence_threshold, light_zone.

        Returns:
            Created machine record (RTSP URL redacted).

        Raises:
            ValueError: If machine_id is invalid or RTSP URL is malformed.
            DuplicateMachineError: If machine_id already exists.
        """
        machine_id = config["machine_id"]
        display_name = config["display_name"]
        rtsp_url = config["rtsp_url"]
        detection_zone = config.get("detection_zone", "(0.0, 0.0, 1.0, 1.0)")
        person_confidence_threshold = config.get("person_confidence_threshold", 0.60)
        light_zone = config.get("light_zone")

        # Validate
        if not validate_machine_id(machine_id):
            raise ValueError(
                f"Invalid machine_id '{machine_id}': must be alphanumeric + hyphens, 1-20 chars"
            )
        if not validate_rtsp_url(rtsp_url):
            raise ValueError(f"Invalid RTSP URL: must be rtsp:// or rtsps:// scheme with a hostname")

        # Check uniqueness
        existing = await self._db.fetch_one(
            "SELECT machine_id FROM machines WHERE machine_id = ?", (machine_id,)
        )
        if existing:
            raise DuplicateMachineError(f"Machine '{machine_id}' already exists")

        # Encrypt RTSP URL
        encrypted_url = encrypt_rtsp_url(rtsp_url)
        now = datetime.utcnow().isoformat()

        await self._db.execute(
            """INSERT INTO machines (machine_id, display_name, rtsp_url_encrypted,
               detection_zone, person_confidence_threshold, light_zone, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (machine_id, display_name, encrypted_url, detection_zone,
             person_confidence_threshold, light_zone, now, now),
        )

        logger.info("Machine registered: %s (%s)", machine_id, display_name)
        return await self.get(machine_id)

    async def get(self, machine_id: str) -> Optional[Dict[str, Any]]:
        """Get a single machine config by ID. Returns None if not found."""
        row = await self._db.fetch_one(
            "SELECT * FROM machines WHERE machine_id = ?", (machine_id,)
        )
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_all(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all machine configs, optionally filtered by status."""
        if status:
            rows = await self._db.fetch_all(
                "SELECT * FROM machines WHERE status = ? ORDER BY created_at", (status,)
            )
        else:
            rows = await self._db.fetch_all(
                "SELECT * FROM machines ORDER BY created_at"
            )
        return [self._row_to_dict(r) for r in rows]

    async def update(self, machine_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a machine's configuration fields.

        Args:
            machine_id: The machine to update.
            updates: Dict of fields to update (display_name, rtsp_url, detection_zone,
                     person_confidence_threshold, light_zone).

        Returns:
            Updated machine record, or None if machine not found.
        """
        existing = await self._db.fetch_one(
            "SELECT * FROM machines WHERE machine_id = ?", (machine_id,)
        )
        if existing is None:
            return None

        set_clauses = []
        params = []

        if "display_name" in updates and updates["display_name"] is not None:
            set_clauses.append("display_name = ?")
            params.append(updates["display_name"])

        if "rtsp_url" in updates and updates["rtsp_url"] is not None:
            url = updates["rtsp_url"]
            if not validate_rtsp_url(url):
                raise ValueError(f"Invalid RTSP URL: must be rtsp:// or rtsps:// scheme with a hostname")
            set_clauses.append("rtsp_url_encrypted = ?")
            params.append(encrypt_rtsp_url(url))

        if "detection_zone" in updates and updates["detection_zone"] is not None:
            set_clauses.append("detection_zone = ?")
            params.append(updates["detection_zone"])

        if "person_confidence_threshold" in updates and updates["person_confidence_threshold"] is not None:
            set_clauses.append("person_confidence_threshold = ?")
            params.append(updates["person_confidence_threshold"])

        if "light_zone" in updates:
            set_clauses.append("light_zone = ?")
            params.append(updates["light_zone"])

        if not set_clauses:
            return await self.get(machine_id)

        set_clauses.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(machine_id)

        sql = f"UPDATE machines SET {', '.join(set_clauses)} WHERE machine_id = ?"
        await self._db.execute(sql, tuple(params))

        logger.info("Machine updated: %s (fields: %s)", machine_id, list(updates.keys()))
        return await self.get(machine_id)

    async def deactivate(self, machine_id: str) -> bool:
        """Soft-delete: set machine status to 'inactive'.

        Returns True if machine was found and deactivated, False if not found.
        """
        existing = await self._db.fetch_one(
            "SELECT machine_id FROM machines WHERE machine_id = ?", (machine_id,)
        )
        if existing is None:
            return False

        now = datetime.utcnow().isoformat()
        await self._db.execute(
            "UPDATE machines SET status = 'inactive', updated_at = ? WHERE machine_id = ?",
            (now, machine_id),
        )
        logger.info("Machine deactivated: %s", machine_id)
        return True

    async def activate(self, machine_id: str) -> Optional[Dict[str, Any]]:
        """Reactivate an inactive machine.

        Returns updated machine record, or None if not found.
        """
        existing = await self._db.fetch_one(
            "SELECT machine_id FROM machines WHERE machine_id = ?", (machine_id,)
        )
        if existing is None:
            return None

        now = datetime.utcnow().isoformat()
        await self._db.execute(
            "UPDATE machines SET status = 'active', updated_at = ? WHERE machine_id = ?",
            (now, machine_id),
        )
        logger.info("Machine activated: %s", machine_id)
        return await self.get(machine_id)

    async def get_decrypted_url(self, machine_id: str) -> Optional[str]:
        """Get the decrypted RTSP URL for pipeline startup. Never log this."""
        row = await self._db.fetch_one(
            "SELECT rtsp_url_encrypted FROM machines WHERE machine_id = ?", (machine_id,)
        )
        if row is None:
            return None
        return decrypt_rtsp_url(row["rtsp_url_encrypted"])

    def _row_to_dict(self, row) -> Dict[str, Any]:
        """Convert a DB row to a response dict with redacted RTSP URL."""
        # Decrypt URL just to redact it (strip credentials)
        try:
            decrypted = decrypt_rtsp_url(row["rtsp_url_encrypted"])
            redacted = redact_rtsp_url(decrypted)
        except Exception:
            redacted = "rtsp://***"

        return {
            "machine_id": row["machine_id"],
            "display_name": row["display_name"],
            "rtsp_url_redacted": redacted,
            "detection_zone": row["detection_zone"],
            "person_confidence_threshold": row["person_confidence_threshold"],
            "light_zone": row["light_zone"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


class CloudMachineRegistry:
    """Credential-free machine metadata read path for the Cloud_Server.

    The Cloud_Server is the authoritative source for Machine_Metadata
    (Requirement 7.1) but must never store, read, return, or log RTSP URLs or
    camera credentials (Requirements 13.2, 13.3). This variant queries ONLY the
    non-secret metadata columns (``_METADATA_COLUMNS``) — the
    ``rtsp_url_encrypted`` column is never referenced — and exposes:

    - ``get_metadata`` / ``list_metadata`` — the credential-free read path used
      by the ``GET /api/ingest/machines`` metadata pull, returning
      ``MachineMetadata`` (which itself carries no credential fields).
    - ``is_registered`` — a lightweight machine-registration lookup used by the
      Ingest_API to validate that an incoming payload's machine ID is known
      before persisting (Requirement 2.8).

    Read-only: registration/update/credential operations remain on the edge and
    the authoritative ``MachineRegistry``; this class never decrypts anything.
    """

    def __init__(self, db: AsyncDatabase):
        self._db = db

    async def get_metadata(self, machine_id: str) -> Optional[MachineMetadata]:
        """Return credential-free ``MachineMetadata`` for one machine, or None.

        Selects only metadata columns; never reads ``rtsp_url_encrypted``.
        """
        row = await self._db.fetch_one(
            f"SELECT {_METADATA_SELECT} FROM machines WHERE machine_id = ?",
            (machine_id,),
        )
        if row is None:
            return None
        return self._row_to_metadata(row)

    async def list_metadata(
        self, status: Optional[str] = "active"
    ) -> List[MachineMetadata]:
        """Return credential-free ``MachineMetadata`` for all machines.

        Defaults to only ``active`` machines (pass ``status=None`` for all).
        Selects only metadata columns; never reads ``rtsp_url_encrypted``.
        """
        if status:
            rows = await self._db.fetch_all(
                f"SELECT {_METADATA_SELECT} FROM machines "
                "WHERE status = ? ORDER BY created_at",
                (status,),
            )
        else:
            rows = await self._db.fetch_all(
                f"SELECT {_METADATA_SELECT} FROM machines ORDER BY created_at"
            )
        return [self._row_to_metadata(r) for r in rows]

    async def is_registered(self, machine_id: str) -> bool:
        """Return True if the machine ID exists in the Machine_Registry.

        Machine-registration lookup used by the Ingest_API to validate machine
        IDs before persisting (Requirement 2.8). Reads only ``machine_id``.
        """
        row = await self._db.fetch_one(
            "SELECT 1 FROM machines WHERE machine_id = ?", (machine_id,)
        )
        return row is not None

    @staticmethod
    def _row_to_metadata(row) -> MachineMetadata:
        """Map a metadata-only DB row to a ``MachineMetadata`` model.

        No credential column is present in ``row`` by construction, so the
        resulting model is guaranteed credential-free.
        """
        return MachineMetadata(
            machine_id=row["machine_id"],
            display_name=row["display_name"],
            detection_zone=row["detection_zone"],
            person_confidence_threshold=row["person_confidence_threshold"],
            light_zone=row["light_zone"],
            updated_at=row["updated_at"],
        )


class DuplicateMachineError(Exception):
    """Raised when attempting to register a machine with an existing ID."""
    pass
