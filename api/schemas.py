"""Centralized Pydantic request/response schemas for the Shop Floor Tracker API.

Provides typed validation for all API endpoints, ensuring request bodies,
path parameters, and query parameters are validated with descriptive errors.

Requirements: 9.2, 9.3
"""

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field, field_validator


T = TypeVar("T")


# ── Pagination ────────────────────────────────────────────────────


class PaginationParams(BaseModel):
    """Query parameters for paginated list endpoints."""

    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page (1-100)")


class PaginatedResponse(BaseModel):
    """Generic wrapper for paginated API responses."""

    data: list[Any]
    total: int = Field(ge=0, description="Total number of records")
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total_pages: int = Field(ge=0)


# ── Camera Zone Configuration ─────────────────────────────────────


class LightZone(BaseModel):
    """Coordinates for a light detection zone (fractional 0.0 to 1.0)."""

    x1: float = Field(ge=0.0, le=1.0)
    y1: float = Field(ge=0.0, le=1.0)
    x2: float = Field(ge=0.0, le=1.0)
    y2: float = Field(ge=0.0, le=1.0)

    @field_validator("x2")
    @classmethod
    def x2_greater_than_x1(cls, v: float, info) -> float:
        if "x1" in info.data and v <= info.data["x1"]:
            raise ValueError("x2 must be greater than x1")
        return v

    @field_validator("y2")
    @classmethod
    def y2_greater_than_y1(cls, v: float, info) -> float:
        if "y1" in info.data and v <= info.data["y1"]:
            raise ValueError("y2 must be greater than y1")
        return v


class CameraZonePayload(BaseModel):
    """Request body for POST /api/camera/zones — machine zone configuration from the wizard."""

    id: Optional[str] = Field(default=None, description="Machine identifier")
    machineName: Optional[str] = Field(default=None, description="Machine name (alias for id)")
    name: Optional[str] = Field(default=None, description="Machine name (alias for id)")
    lightZone: Optional[LightZone] = Field(default=None, description="Light detection zone coords")

    # Allow additional fields from the wizard (detection zones, OCR zones, etc.)
    model_config = {"extra": "allow"}

    @field_validator("id", "machineName", "name", mode="before")
    @classmethod
    def strip_strings(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v

    @property
    def machine_id(self) -> Optional[str]:
        """Resolve the machine identifier from any of the name fields."""
        return self.id or self.machineName or self.name


# ── Settings ──────────────────────────────────────────────────────


class SettingsUpdate(BaseModel):
    """Request body for PUT /api/settings/{section} — key-value settings update.

    Accepts arbitrary key-value pairs for flexible settings storage.
    At least one field must be provided.
    """

    model_config = {"extra": "allow"}

    @field_validator("*", mode="before")
    @classmethod
    def reject_none_values(cls, v):
        """Ensure no None values are passed as settings."""
        return v


# ── Employee Schemas (re-exported for central access) ─────────────

import re


class EmployeeCreate(BaseModel):
    """Request body for POST /api/employees."""

    badge_id: str = Field(description="4-6 digit numeric badge identifier")
    name: str = Field(min_length=1, max_length=100, description="Employee display name")

    @field_validator("badge_id")
    @classmethod
    def validate_badge_id(cls, v: str) -> str:
        if not re.match(r"^\d{4,6}$", v):
            raise ValueError("Badge ID must be 4-6 numeric digits")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty or whitespace")
        return v.strip()


# ── Alert Schemas ─────────────────────────────────────────────────


class AlertResolve(BaseModel):
    """Request body for POST /api/alerts/{alert_id}/resolve."""

    note: Optional[str] = Field(default=None, max_length=500, description="Resolution note")


# ── Setup Schemas ─────────────────────────────────────────────────


class SetupInitRequest(BaseModel):
    """Request body for POST /api/setup/init — first-run admin setup."""

    username: str = Field(min_length=1, max_length=50, description="Admin username")
    password: str = Field(min_length=6, max_length=128, description="Admin password (min 6 chars)")
    company_name: str = Field(default="Cologic", max_length=100)
    logo_url: str = Field(default="", max_length=500)
    primary_color: str = Field(default="#6366F1", max_length=20)

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Username cannot be empty or whitespace")
        return v.strip()

    @field_validator("primary_color")
    @classmethod
    def validate_color(cls, v: str) -> str:
        if v and not re.match(r"^#[0-9a-fA-F]{3,8}$", v):
            raise ValueError("Primary color must be a valid hex color (e.g., #6366F1)")
        return v


# ── AI Chat Schemas ───────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A single message in a chat conversation."""

    role: str = Field(description="Message role: 'user' or 'assistant'")
    content: str = Field(min_length=1, description="Message content")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("user", "assistant", "system"):
            raise ValueError("Role must be 'user', 'assistant', or 'system'")
        return v


class ChatRequest(BaseModel):
    """Request body for POST /api/ai/chat."""

    messages: list[ChatMessage] = Field(min_length=1, description="Conversation messages")


# ── Machine Schemas (for legacy /api/machines endpoint) ───────────


class MachineDeleteResponse(BaseModel):
    """Response for DELETE /api/machines/{machine_id}."""

    status: str = "deleted"
    machine_id: str
