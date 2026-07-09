"""Object_Store — Cloud_Server event-image storage (Google Cloud Storage).

The Cloud_Server stores annotated Event_Images captured by the Edge_Agent at
alert time and references them by URL from the persisted Alert record. This
module wraps the GCS client behind a small, testable interface.

Key design points:
- Deterministic object key ``alerts/{machine_id}/{event_id}.jpg`` so that a
  retried upload of the *same* alert overwrites rather than creating a
  duplicate object (Requirement 8.3, idempotent-friendly).
- Bucket and credentials are read from environment configuration
  (``config.GCS_BUCKET`` / ``GOOGLE_APPLICATION_CREDENTIALS``), never
  hardcoded and never committed (Requirement 14.5).
- The ``google-cloud-storage`` dependency is imported lazily so this module
  imports cleanly even when the package is not installed. When GCS is
  unavailable or unconfigured, an in-memory fake is used instead, which is
  also what tests exercise.

Requirements: 8.2, 8.3, 14.5
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

# Object-key prefix and extension for alert event images.
_KEY_TEMPLATE = "alerts/{machine_id}/{event_id}.jpg"
_CONTENT_TYPE = "image/jpeg"


class ObjectStoreError(Exception):
    """Raised when an Object_Store operation fails."""


def build_object_key(machine_id: str, event_id: str) -> str:
    """Return the deterministic object key for an alert Event_Image.

    The key is a pure function of ``(machine_id, event_id)`` so repeated
    uploads of the same alert address the same object and overwrite in place.
    """
    if not machine_id:
        raise ValueError("machine_id must be a non-empty string")
    if not event_id:
        raise ValueError("event_id must be a non-empty string")
    return _KEY_TEMPLATE.format(machine_id=machine_id, event_id=event_id)


@runtime_checkable
class ObjectStore(Protocol):
    """Interface for storing alert Event_Images and returning their URL."""

    def upload_event_image(
        self, machine_id: str, event_id: str, jpeg_bytes: bytes
    ) -> str:
        """Upload ``jpeg_bytes`` for the given alert and return its URL."""
        ...


class GCSObjectStore:
    """Object_Store backed by Google Cloud Storage.

    The ``google.cloud.storage`` import is deferred to construction time so
    importing this module never requires the package to be installed.
    """

    def __init__(
        self,
        bucket: str,
        credentials_path: Optional[str] = None,
    ):
        if not bucket:
            raise ValueError("bucket must be a non-empty string")

        try:
            from google.cloud import storage  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised via factory
            raise ObjectStoreError(
                "google-cloud-storage is not installed; cannot use GCSObjectStore"
            ) from exc

        self._bucket_name = bucket
        if credentials_path:
            self._client = storage.Client.from_service_account_json(credentials_path)
        else:
            # Application Default Credentials (ADC).
            self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)

    def upload_event_image(
        self, machine_id: str, event_id: str, jpeg_bytes: bytes
    ) -> str:
        key = build_object_key(machine_id, event_id)
        try:
            blob = self._bucket.blob(key)
            blob.upload_from_string(jpeg_bytes, content_type=_CONTENT_TYPE)
        except Exception as exc:  # pragma: no cover - network dependent
            logger.error(
                "Object_Store upload failed",
                machine_id=machine_id,
                event_id=event_id,
                bucket=self._bucket_name,
                error=str(exc),
            )
            raise ObjectStoreError(f"failed to upload event image: {exc}") from exc

        url = f"https://storage.googleapis.com/{self._bucket_name}/{key}"
        logger.info(
            "Object_Store upload ok",
            machine_id=machine_id,
            event_id=event_id,
            url=url,
        )
        return url


class InMemoryObjectStore:
    """In-memory Object_Store fake for development and tests.

    Stores uploaded bytes keyed by object key (last-write-wins, matching the
    overwrite semantics of the deterministic GCS key) and returns a stable
    ``memory://`` URL. Exposes ``objects`` and ``get`` so tests can assert on
    what was stored.
    """

    def __init__(self, bucket: str = "in-memory"):
        self._bucket = bucket
        self._objects: Dict[str, bytes] = {}
        self._lock = threading.Lock()

    def upload_event_image(
        self, machine_id: str, event_id: str, jpeg_bytes: bytes
    ) -> str:
        key = build_object_key(machine_id, event_id)
        with self._lock:
            self._objects[key] = bytes(jpeg_bytes)
        url = f"memory://{self._bucket}/{key}"
        logger.debug(
            "InMemoryObjectStore stored image",
            machine_id=machine_id,
            event_id=event_id,
            url=url,
        )
        return url

    def get(self, machine_id: str, event_id: str) -> Optional[bytes]:
        """Return the stored bytes for an alert image, or None if absent."""
        key = build_object_key(machine_id, event_id)
        with self._lock:
            return self._objects.get(key)

    @property
    def objects(self) -> Dict[str, bytes]:
        """Return a snapshot copy of stored objects keyed by object key."""
        with self._lock:
            return dict(self._objects)


def get_object_store(
    bucket: Optional[str] = None,
    credentials_path: Optional[str] = None,
) -> ObjectStore:
    """Return the configured Object_Store.

    Resolution order:
    1. If a bucket is configured (argument or ``config.GCS_BUCKET``) and
       ``google-cloud-storage`` is importable, return a ``GCSObjectStore``.
    2. Otherwise fall back to an ``InMemoryObjectStore`` and log a warning.

    This keeps the module usable without the GCS package installed while still
    reading credentials/bucket from environment config in production.
    """
    if bucket is None or credentials_path is None:
        try:
            import config

            if bucket is None:
                bucket = getattr(config, "GCS_BUCKET", "") or ""
            if credentials_path is None:
                credentials_path = (
                    getattr(config, "GOOGLE_APPLICATION_CREDENTIALS", "") or ""
                )
        except Exception:  # pragma: no cover - config always importable here
            bucket = bucket or ""
            credentials_path = credentials_path or ""

    if bucket:
        try:
            return GCSObjectStore(bucket, credentials_path or None)
        except ObjectStoreError as exc:
            logger.warning(
                "GCS Object_Store unavailable, falling back to in-memory store",
                bucket=bucket,
                error=str(exc),
            )
    else:
        logger.warning(
            "GCS_BUCKET not configured — using in-memory Object_Store "
            "(development/tests only)"
        )

    return InMemoryObjectStore()
