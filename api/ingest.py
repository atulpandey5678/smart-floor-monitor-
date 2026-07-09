"""Cloud_Server Ingest_API router (`/api/ingest/*`).

This router receives pushed data from the Edge_Agent's Sync_Client. Every route
is gated by the dedicated API-key dependency :func:`verify_ingest_key` (staff
cookies are never accepted here — Requirements 3.3, 3.5) and the whole prefix is
exempted from the cookie ``AuthMiddleware`` and CSRF middleware in
``api/server.py``. The oversize-body guard middleware
(:class:`api.ingest_body_guard.IngestBodySizeGuardMiddleware`) already rejects
bodies over the configured maximum with HTTP 413 before parsing (Requirement
2.9), so the handlers below never see an oversized payload.

Per write endpoint the flow is (Requirement 2.5, 2.8, 2.10):

    validate API key (dependency)
      -> validate schema (FastAPI -> 422 with per-field errors on failure)
      -> enforce max body size (413, middleware)
      -> verify the machine ID is registered (422 if unknown)
      -> persist idempotently by Event_ID
      -> 200 with the accepted Event_ID.

Endpoints:

    POST /api/ingest/session        -> 200 {event_id, status}
    POST /api/ingest/alert          -> 200 {event_id, status}   (uploads image)
    POST /api/ingest/status         -> 200 {machine_id, liveness} (heartbeat)
    POST /api/ingest/machine-event  -> 200 {event_id, status}
    GET  /api/ingest/machines       -> 200 [MachineMetadata]

Dependencies (repository, Live_State_Cache, CloudMachineRegistry, Object_Store)
are injected at startup by ``api/server.py``'s lifespan through the ``set_*``
setters below, mirroring the ``api/routes.py`` ``set_repo`` pattern.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.8, 2.10, 3.3, 8.4
"""

from __future__ import annotations

import base64
import binascii
from typing import List, Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from api.ingest_auth import verify_ingest_key
from api.ingest_schemas import (
    AlertMsg,
    Heartbeat,
    MachineEventMsg,
    MachineMetadata,
    SessionRecordMsg,
)
from engine.object_store import ObjectStore, get_object_store
from engine.snapshot_store import SnapshotStore, get_snapshot_store

logger = structlog.get_logger(__name__)


# ── Shared state (injected from server.py lifespan) ───────────────
# Populated during application startup via the setters below, following the
# same module-level dependency-injection convention as ``api/routes.py``.
_repo = None
_live_cache = None
_registry = None
_object_store: Optional[ObjectStore] = None
_snapshot_store: Optional[SnapshotStore] = None


def set_ingest_repo(repo) -> None:
    """Inject the async-DB-backed Repository used for idempotent persistence."""
    global _repo
    _repo = repo


def set_ingest_live_cache(cache) -> None:
    """Inject the Live_State_Cache fed by heartbeats (``POST /status``)."""
    global _live_cache
    _live_cache = cache


def set_ingest_registry(registry) -> None:
    """Inject the CloudMachineRegistry used for machine-ID validation + metadata."""
    global _registry
    _registry = registry


def set_ingest_object_store(store: ObjectStore) -> None:
    """Inject the Object_Store used for alert Event_Image uploads (tests/DI)."""
    global _object_store
    _object_store = store


def _get_object_store() -> ObjectStore:
    """Return the module-level Object_Store, lazily building it from config."""
    global _object_store
    if _object_store is None:
        _object_store = get_object_store()
    return _object_store


def set_ingest_snapshot_store(store: SnapshotStore) -> None:
    """Inject the Snapshot_Store used for most-recent snapshot uploads (tests/DI)."""
    global _snapshot_store
    _snapshot_store = store


def _get_snapshot_store() -> SnapshotStore:
    """Return the shared Snapshot_Store (last-write-wins per machine, Req 9.4).

    Falls back to the process-wide singleton so the staff Dashboard read route
    and this ingest writer share the same instance without server.py wiring.
    """
    global _snapshot_store
    if _snapshot_store is None:
        _snapshot_store = get_snapshot_store()
    return _snapshot_store


# ── Router (API-key gated) ────────────────────────────────────────

router = APIRouter(prefix="/api/ingest", dependencies=[Depends(verify_ingest_key)])


# ── Internal guards ───────────────────────────────────────────────


def _require_repo():
    if _repo is None:
        raise HTTPException(status_code=503, detail="Ingest service not ready")
    return _repo


async def _verify_machine_registered(machine_id: str) -> None:
    """Reject ingest payloads for unknown machine IDs with HTTP 422 (Req 2.8).

    Persists nothing when the machine ID is not in the Machine_Registry; the
    error message identifies the offending machine ID.
    """
    if _registry is None:
        raise HTTPException(status_code=503, detail="Ingest service not ready")
    if not await _registry.is_registered(machine_id):
        raise HTTPException(
            status_code=422,
            detail=f"Unknown machine ID '{machine_id}': not registered in the Machine_Registry",
        )


# ── Write endpoints ───────────────────────────────────────────────


@router.post("/session")
async def ingest_session(msg: SessionRecordMsg):
    """Persist a Session_Record push (open/update/close), idempotent by Event_ID."""
    repo = _require_repo()
    await _verify_machine_registered(msg.machine_id)
    result = await repo.ingest_session(msg)
    return {"event_id": result.event_id, "status": result.status}


@router.post("/alert")
async def ingest_alert(msg: AlertMsg):
    """Persist an Alert push and upload its Event_Image to the Object_Store.

    The base64 image is decoded, uploaded via the Object_Store, and the
    returned URL is stored on the alert row in the same transaction
    (Requirements 8.2, 8.3, 8.4). Idempotent by Event_ID.
    """
    repo = _require_repo()
    await _verify_machine_registered(msg.machine_id)

    try:
        image_bytes = base64.b64decode(msg.event_image_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        # Malformed image payload — reject atomically with a per-field 422.
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "loc": ["body", "event_image_b64"],
                    "msg": f"event_image_b64 is not valid base64: {exc}",
                    "type": "value_error.base64",
                }
            ],
        )

    # Only Alert events trigger an Object_Store upload (Requirement 8.4). The
    # deterministic key means a retried duplicate overwrites in place.
    store = _get_object_store()
    image_url = store.upload_event_image(msg.machine_id, msg.event_id, image_bytes)

    result = await repo.ingest_alert(msg, image_url)
    return {"event_id": result.event_id, "status": result.status}


@router.post("/status")
async def ingest_status(hb: Heartbeat):
    """Feed a Heartbeat into the Live_State_Cache (not idempotent-keyed).

    The Heartbeat schema is validated by FastAPI (422 on failure). A valid
    Heartbeat for a registered machine updates the cache entry; an invalid one
    leaves the entry unchanged (Requirements 6.4, 6.5).
    """
    if _live_cache is None:
        raise HTTPException(status_code=503, detail="Ingest service not ready")
    await _verify_machine_registered(hb.machine_id)

    applied, state = await _live_cache.apply_raw_heartbeat(hb)
    if not applied or state is None:
        raise HTTPException(status_code=422, detail="Invalid heartbeat")
    return {"machine_id": hb.machine_id, "liveness": state.liveness}


@router.post("/snapshot")
async def ingest_snapshot(
    request: Request,
    x_machine_id: str = Header(..., alias="X-Machine-Id"),
):
    """Store the most-recent Snapshot_Thumbnail for a machine (best-effort).

    The Edge_Agent sends the reduced-resolution JPEG as the raw request body
    with an ``X-Machine-Id`` header (see ``edge/sync_client.send_snapshot``).
    Snapshots are never persisted to the Database or the Object_Store — only the
    latest one per machine ID is kept in memory, last-write-wins (Requirements
    9.4, 10.1). The oversize-body guard middleware already rejects payloads over
    the configured max with HTTP 413 before this handler runs.
    """
    await _verify_machine_registered(x_machine_id)

    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="Snapshot body is empty")

    content_type = request.headers.get("content-type") or "image/jpeg"
    _get_snapshot_store().put(x_machine_id, body, content_type)
    return {"machine_id": x_machine_id, "bytes": len(body)}


@router.post("/machine-event")
async def ingest_machine_event(msg: MachineEventMsg):
    """Persist a Machine_Event (tower-light transition), idempotent by Event_ID."""
    repo = _require_repo()
    await _verify_machine_registered(msg.machine_id)
    result = await repo.ingest_machine_event(msg)
    return {"event_id": result.event_id, "status": result.status}


# ── Metadata pull ─────────────────────────────────────────────────


@router.get("/machines", response_model=List[MachineMetadata])
async def list_machines() -> List[MachineMetadata]:
    """Return credential-free Machine_Metadata for the Edge_Agent to pull/poll.

    Serves only non-secret metadata columns — never RTSP URLs or camera
    credentials (Requirements 7.1, 13.2, 13.3).
    """
    if _registry is None:
        raise HTTPException(status_code=503, detail="Ingest service not ready")
    return await _registry.list_metadata()
