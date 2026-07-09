"""Pydantic message models for the Cloud_Server Ingest_API.

These schemas define the wire contract between the Edge_Agent's Sync_Client and
the Cloud_Server's ``/api/ingest/*`` endpoints. Every durable push carries an
``event_id`` (idempotency key) and a ``machine_id`` (originating machine tag),
and the ``MachineMetadata`` pulled by the edge is deliberately credential-free:
it MUST NOT contain RTSP URLs or camera credentials (Requirement 13).

Requirements: 6.3, 2.1, 2.2, 2.3, 2.4, 7.1
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ── Shared enum vocabularies ──────────────────────────────────────

# Session_Manager state machine states (Requirement 6.3).
SessionState = Literal["IDLE", "OPENING", "ACTIVE", "GRACE", "ABANDONED", "CLOSED"]

# Machine tower light classifications (Requirements 1.5, 6.3).
LightState = Literal["GREEN", "AMBER", "RED", "OFF", "UNKNOWN"]

# Camera health derived from last-frame age (Requirement 6.3).
CameraHealth = Literal["HEALTHY", "DEGRADED", "FAILED"]


# ── Heartbeat (live status; POST /api/ingest/status) ──────────────


class Heartbeat(BaseModel):
    """Live status message per machine (best-effort; never queued).

    Sent on Session_Manager state change and periodically while a session is
    active. Feeds the cloud Live_State_Cache. See Requirements 6.1-6.5.
    """

    machine_id: str
    state: SessionState
    worker_present: bool
    active_duration_seconds: int = Field(
        ge=0, description="Active session duration in whole seconds (0 when idle)"
    )
    machine_light: LightState
    camera_health: CameraHealth


# ── Session_Record (POST /api/ingest/session) ────────────────────


class SessionRecordMsg(BaseModel):
    """A worker session record push (open, update, or close).

    Idempotent by ``event_id``; the domain row is upserted by ``session_uuid``
    so open/update/close pushes for one session collapse to a single record.
    See Requirements 2.1, 5.3.
    """

    event_id: str
    machine_id: str
    session_uuid: str
    produced_at: datetime
    op: Literal["open", "update", "close"]
    start_time: datetime
    end_time: Optional[datetime] = None
    active_duration_seconds: float = Field(
        ge=0, description="Accumulated active duration in seconds"
    )
    close_reason: Optional[str] = None


# ── Alert (POST /api/ingest/alert) ────────────────────────────────


class AlertMsg(BaseModel):
    """An alert push with an associated base64-encoded Event_Image.

    The image is decoded and size-checked against the 10 MB total-body limit at
    the endpoint, then uploaded to the Object_Store. See Requirements 2.2, 8.2.
    """

    event_id: str
    machine_id: str
    produced_at: datetime
    alert_type: str
    message: Optional[str] = None
    event_image_b64: str = Field(description="Base64-encoded annotated Event_Image")


# ── Machine_Event (POST /api/ingest/machine-event) ────────────────


class MachineEventMsg(BaseModel):
    """A machine tower light state transition event. See Requirement 2.4."""

    event_id: str
    machine_id: str
    produced_at: datetime
    previous_status: LightState
    new_status: LightState


# ── Machine_Metadata (GET /api/ingest/machines) ───────────────────


class MachineMetadata(BaseModel):
    """Credential-free machine configuration pulled by the Edge_Agent.

    The Cloud_Server is the authoritative source for this metadata
    (Requirement 7.1). This model MUST NOT contain RTSP URLs or camera
    credentials; those live only in the edge's Local_Camera_Config
    (Requirements 13.2, 13.3).
    """

    machine_id: str
    display_name: str
    detection_zone: str
    person_confidence_threshold: float
    light_zone: Optional[str] = None
    updated_at: datetime
