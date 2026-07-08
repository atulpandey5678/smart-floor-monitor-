"""Machine station CRUD API endpoints.

Provides versioned REST endpoints for managing machine station configurations.
Uses FastAPI APIRouter with /api/v1 prefix.

Requirements: 1.2, 1.3, 1.5, 1.6, 20.1, 20.4
"""

import structlog
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

from db.async_database import get_async_database
from engine.machine_registry import (
    DuplicateMachineError,
    MachineRegistry,
    validate_machine_id,
    validate_rtsp_url,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


# ── Pydantic Request/Response Models ─────────────────────────────


class MachineCreate(BaseModel):
    """Request body for registering a new machine station."""

    machine_id: str  # alphanumeric + hyphens, 1-20 chars
    display_name: str
    rtsp_url: str
    detection_zone: str = "(0.0, 0.0, 1.0, 1.0)"
    person_confidence_threshold: float = 0.60
    light_zone: Optional[str] = None

    @field_validator("machine_id")
    @classmethod
    def validate_machine_id_format(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z0-9\-]{1,20}$", v):
            raise ValueError(
                "machine_id must be alphanumeric + hyphens, 1-20 characters"
            )
        return v

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp_url_format(cls, v: str) -> str:
        if not validate_rtsp_url(v):
            raise ValueError("rtsp_url must be a valid rtsp:// or rtsps:// URI")
        return v

    @field_validator("person_confidence_threshold")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError("person_confidence_threshold must be between 0.0 and 1.0")
        return v


class MachineUpdate(BaseModel):
    """Request body for updating machine configuration (partial update)."""

    display_name: Optional[str] = None
    rtsp_url: Optional[str] = None
    detection_zone: Optional[str] = None
    person_confidence_threshold: Optional[float] = None
    light_zone: Optional[str] = None

    @field_validator("rtsp_url")
    @classmethod
    def validate_rtsp_url_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not validate_rtsp_url(v):
            raise ValueError("rtsp_url must be a valid rtsp:// or rtsps:// URI")
        return v

    @field_validator("person_confidence_threshold")
    @classmethod
    def validate_confidence(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 < v <= 1.0):
            raise ValueError("person_confidence_threshold must be between 0.0 and 1.0")
        return v


class MachineResponse(BaseModel):
    """Response model for a machine station configuration."""

    machine_id: str
    display_name: str
    rtsp_url_redacted: str  # credentials stripped
    detection_zone: str
    person_confidence_threshold: float
    light_zone: Optional[str]
    status: str
    created_at: str
    updated_at: str


# ── Helper ────────────────────────────────────────────────────────


def _get_registry() -> MachineRegistry:
    """Get a MachineRegistry instance using the global async DB."""
    async_db = get_async_database()
    return MachineRegistry(async_db)


# ── Endpoints ─────────────────────────────────────────────────────


@router.post("/machines", response_model=MachineResponse, status_code=201)
async def register_machine(body: MachineCreate):
    """POST /api/v1/machines — Register a new machine station.

    Returns 201 with created machine config (RTSP URL redacted).
    Returns 409 if machine_id already exists.
    Returns 422 for invalid input (handled by Pydantic).
    """
    registry = _get_registry()
    try:
        machine = await registry.register(body.model_dump())
        return MachineResponse(**machine)
    except DuplicateMachineError:
        raise HTTPException(
            status_code=409,
            detail=f"Machine '{body.machine_id}' already exists",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/machines")
async def list_machines(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """GET /api/v1/machines — List all machine stations (paginated).

    Optional query param `status` to filter by 'active' or 'inactive'.
    """
    if status and status not in ("active", "inactive"):
        raise HTTPException(
            status_code=400,
            detail="status must be 'active' or 'inactive'",
        )
    from api.pagination import PaginationParams, paginated_response
    params = PaginationParams(page, page_size)
    registry = _get_registry()
    machines = await registry.list_all(status=status)
    # Apply pagination in-memory since registry returns all results
    total = len(machines)
    paginated_items = machines[params.offset : params.offset + params.limit]
    return paginated_response(
        [MachineResponse(**m).model_dump() for m in paginated_items],
        total,
        params.page,
        params.page_size,
    )


@router.get("/machines/{machine_id}", response_model=MachineResponse)
async def get_machine(machine_id: str):
    """GET /api/v1/machines/{machine_id} — Get a single machine configuration.

    Returns 404 if not found.
    """
    registry = _get_registry()
    machine = await registry.get(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_id}' not found")
    return MachineResponse(**machine)


@router.put("/machines/{machine_id}", response_model=MachineResponse)
async def update_machine(machine_id: str, body: MachineUpdate):
    """PUT /api/v1/machines/{machine_id} — Update machine configuration.

    Partial update: only provided fields are changed.
    Triggers hot-reload notification (applies update to DB for now).
    Returns 404 if machine not found.
    """
    registry = _get_registry()

    # Build updates dict from non-None fields
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        # Nothing to update — just return current config
        machine = await registry.get(machine_id)
        if machine is None:
            raise HTTPException(status_code=404, detail=f"Machine '{machine_id}' not found")
        return MachineResponse(**machine)

    try:
        machine = await registry.update(machine_id, updates)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if machine is None:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_id}' not found")

    return MachineResponse(**machine)


@router.delete("/machines/{machine_id}", status_code=204)
async def deactivate_machine(machine_id: str):
    """DELETE /api/v1/machines/{machine_id} — Soft-delete (deactivate) a machine.

    Sets status to 'inactive'. Returns 204 No Content.
    Returns 404 if machine not found.
    """
    registry = _get_registry()
    success = await registry.deactivate(machine_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_id}' not found")
    return None


@router.post("/machines/{machine_id}/activate", response_model=MachineResponse)
async def activate_machine(machine_id: str):
    """POST /api/v1/machines/{machine_id}/activate — Reactivate an inactive machine.

    Returns 200 with the updated machine configuration.
    Returns 404 if machine not found.
    """
    registry = _get_registry()
    machine = await registry.activate(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_id}' not found")
    return MachineResponse(**machine)
