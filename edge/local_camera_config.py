"""Local_Camera_Config — the Edge_Agent's secret camera connection mapping.

The Cloud_Server is the authoritative source for credential-free
``MachineMetadata`` (machine ID, display name, detection zones, thresholds,
light zone). RTSP URLs and camera credentials, however, live **only** on the
Edge_Agent, in a git-excluded local file keyed by machine ID (Requirements 7.5,
7.6, 13.1). This module:

1. Loads that git-excluded mapping file and fails fast at startup when it is
   missing or malformed (Requirement 14.4 startup contract).
2. Merges each cloud ``MachineMetadata`` with the local RTSP entry to build the
   ``machine_config`` dict that ``PipelineOrchestrator.start_pipeline()``
   already expects (``machine_id``, ``rtsp_url``, ``display_name``,
   ``detection_zone``, ``person_confidence_threshold``, ``light_zone``).
3. Skips — and logs a warning for — any metadata machine ID that has no local
   mapping, rather than starting a pipeline without a camera (Requirement 7.8).

The mapping file (default ``camera_config.json``) is JSON of the form::

    {
      "M-01": {
        "rtsp_url": "rtsp://192.168.1.10:554/Streaming/Channels/101",
        "username": "admin",
        "password": "s3cret"
      },
      "M-02": {
        "rtsp_url_encrypted": "gAAAAAB..."     # Fernet-encrypted full URL
      }
    }

An entry may supply either a plaintext ``rtsp_url`` or a Fernet
``rtsp_url_encrypted`` (decrypted via the edge crypto helpers). Separate
``username``/``password`` fields, when present and not already embedded in the
URL, are injected into the URL's userinfo. Credentials are never logged — only
redacted URLs appear in log output.

Requirements: 7.5, 7.6, 7.8, 13.1
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urlparse, urlunparse

import structlog

from api.ingest_schemas import MachineMetadata
from edge.camera_crypto import decrypt_rtsp_url, redact_rtsp_url, validate_rtsp_url

logger = structlog.get_logger(__name__)

# Default git-excluded mapping filename (relative to the Edge_Agent working dir).
DEFAULT_CONFIG_PATH = "camera_config.json"


class LocalCameraConfigError(Exception):
    """Raised when Local_Camera_Config is missing or malformed.

    The Edge_Agent bootstrap treats this as a fatal, fail-fast startup error
    (Requirement 14.4): a missing or malformed camera mapping means the on-site
    cameras cannot be reached, so the agent must not start silently.
    """


@dataclass(frozen=True)
class CameraEntry:
    """A resolved local camera mapping for one machine ID.

    ``rtsp_url`` is the effective connection URL with any separate
    ``username``/``password`` already injected. Its value is a secret and must
    never be transmitted to the cloud or logged unredacted.
    """

    machine_id: str
    rtsp_url: str

    @property
    def redacted_url(self) -> str:
        """Credential-stripped URL safe for logs and diagnostics."""
        return redact_rtsp_url(self.rtsp_url)


def _resolve_rtsp_url(machine_id: str, entry: Dict[str, Any]) -> str:
    """Resolve an entry's effective RTSP URL, injecting credentials if given.

    Accepts either a plaintext ``rtsp_url`` or a Fernet ``rtsp_url_encrypted``.
    When separate ``username``/``password`` fields are supplied and the URL has
    no embedded userinfo, they are injected. Raises ``LocalCameraConfigError``
    for any missing/invalid URL so the caller can fail fast.
    """
    if not isinstance(entry, dict):
        raise LocalCameraConfigError(
            f"machine '{machine_id}': entry must be an object, got "
            f"{type(entry).__name__}"
        )

    raw_url: Optional[str] = None
    encrypted = entry.get("rtsp_url_encrypted")
    if encrypted:
        if not isinstance(encrypted, str):
            raise LocalCameraConfigError(
                f"machine '{machine_id}': 'rtsp_url_encrypted' must be a string"
            )
        try:
            raw_url = decrypt_rtsp_url(encrypted)
        except Exception as exc:  # noqa: BLE001 — surface as config error
            raise LocalCameraConfigError(
                f"machine '{machine_id}': failed to decrypt 'rtsp_url_encrypted'"
            ) from exc
    else:
        raw_url = entry.get("rtsp_url")

    if not raw_url or not isinstance(raw_url, str):
        raise LocalCameraConfigError(
            f"machine '{machine_id}': entry must define 'rtsp_url' or "
            f"'rtsp_url_encrypted'"
        )

    # Inject separate credentials into the URL userinfo when not already present.
    username = entry.get("username")
    password = entry.get("password")
    if username or password:
        parsed = urlparse(raw_url)
        if not parsed.username and not parsed.password:
            userinfo = quote(str(username or ""), safe="")
            if password is not None:
                userinfo += ":" + quote(str(password), safe="")
            host = parsed.hostname or ""
            netloc = f"{userinfo}@{host}"
            if parsed.port:
                netloc += f":{parsed.port}"
            raw_url = urlunparse(parsed._replace(netloc=netloc))

    if not validate_rtsp_url(raw_url):
        # Log only the redacted form so credentials never reach the log.
        raise LocalCameraConfigError(
            f"machine '{machine_id}': invalid RTSP URL "
            f"'{redact_rtsp_url(raw_url)}' — must be rtsp:// or rtsps:// with a host"
        )

    return raw_url


class LocalCameraConfig:
    """Loaded, validated mapping of machine ID -> local camera connection."""

    def __init__(self, entries: Dict[str, CameraEntry]):
        self._entries = dict(entries)

    # ── Loading ───────────────────────────────────────────
    @classmethod
    def load(cls, path: str = DEFAULT_CONFIG_PATH) -> "LocalCameraConfig":
        """Load and validate the git-excluded mapping file (fail fast).

        Raises ``LocalCameraConfigError`` if the file is missing, is not valid
        JSON, is not a JSON object, or contains a malformed/invalid entry.
        """
        if not os.path.isfile(path):
            raise LocalCameraConfigError(
                f"Local_Camera_Config file not found: {path!r}. Create a "
                f"git-excluded mapping of machine_id -> camera connection."
            )

        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise LocalCameraConfigError(
                f"Local_Camera_Config {path!r} is not valid JSON: {exc}"
            ) from exc
        except OSError as exc:
            raise LocalCameraConfigError(
                f"Local_Camera_Config {path!r} could not be read: {exc}"
            ) from exc

        return cls.from_mapping(raw, source=path)

    @classmethod
    def from_mapping(
        cls, raw: Any, source: str = "<mapping>"
    ) -> "LocalCameraConfig":
        """Build and validate from an already-parsed mapping object."""
        if not isinstance(raw, dict):
            raise LocalCameraConfigError(
                f"Local_Camera_Config {source!r} must be a JSON object mapping "
                f"machine_id -> connection, got {type(raw).__name__}"
            )

        entries: Dict[str, CameraEntry] = {}
        for machine_id, entry in raw.items():
            if not isinstance(machine_id, str) or not machine_id:
                raise LocalCameraConfigError(
                    f"Local_Camera_Config {source!r}: machine_id keys must be "
                    f"non-empty strings"
                )
            rtsp_url = _resolve_rtsp_url(machine_id, entry)
            entries[machine_id] = CameraEntry(
                machine_id=machine_id, rtsp_url=rtsp_url
            )

        logger.info(
            "Local_Camera_Config loaded",
            source=source,
            machine_count=len(entries),
            machine_ids=sorted(entries.keys()),
        )
        return cls(entries)

    # ── Accessors ─────────────────────────────────────────
    def get(self, machine_id: str) -> Optional[CameraEntry]:
        """Return the camera entry for ``machine_id`` or None if unmapped."""
        return self._entries.get(machine_id)

    def has(self, machine_id: str) -> bool:
        """Return True if a local mapping exists for ``machine_id``."""
        return machine_id in self._entries

    def machine_ids(self) -> List[str]:
        """Return the sorted list of locally mapped machine IDs."""
        return sorted(self._entries.keys())

    def __len__(self) -> int:
        return len(self._entries)

    # ── Merge with cloud metadata ─────────────────────────
    def build_machine_configs(
        self, metadata: Iterable[MachineMetadata]
    ) -> List[Dict[str, Any]]:
        """Merge cloud ``MachineMetadata`` with local RTSP config.

        Produces one ``machine_config`` dict per machine ID present in **both**
        the cloud metadata and the Local_Camera_Config, shaped exactly as
        ``PipelineOrchestrator.start_pipeline()`` expects. Metadata machine IDs
        with no local mapping are skipped and logged as a warning
        (Requirement 7.8); RTSP URLs/credentials never appear in the warning.
        """
        configs: List[Dict[str, Any]] = []
        skipped = 0
        for meta in metadata:
            entry = self._entries.get(meta.machine_id)
            if entry is None:
                skipped += 1
                logger.warning(
                    "No Local_Camera_Config mapping for machine — skipping "
                    "pipeline start",
                    machine_id=meta.machine_id,
                )
                continue

            configs.append(
                {
                    "machine_id": meta.machine_id,
                    "rtsp_url": entry.rtsp_url,
                    "display_name": meta.display_name,
                    "detection_zone": meta.detection_zone,
                    "person_confidence_threshold": meta.person_confidence_threshold,
                    "light_zone": meta.light_zone,
                }
            )

        logger.info(
            "Built machine configs from cloud metadata + Local_Camera_Config",
            started=len(configs),
            skipped_unmapped=skipped,
        )
        return configs
