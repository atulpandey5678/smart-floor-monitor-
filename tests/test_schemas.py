"""Tests for api/schemas.py — Pydantic input validation schemas.

Validates that request body payloads are properly validated against defined schemas
and return descriptive error messages for invalid input (Requirement 9.2, 9.3).
"""

import pytest
from pydantic import ValidationError

from api.schemas import (
    AlertResolve,
    CameraZonePayload,
    ChatMessage,
    ChatRequest,
    EmployeeCreate,
    LightZone,
    PaginatedResponse,
    PaginationParams,
    SetupInitRequest,
    SettingsUpdate,
)


class TestPaginationParams:
    """Validates pagination query parameter constraints."""

    def test_defaults(self):
        params = PaginationParams()
        assert params.page == 1
        assert params.page_size == 20

    def test_valid_values(self):
        params = PaginationParams(page=3, page_size=50)
        assert params.page == 3
        assert params.page_size == 50

    def test_page_must_be_positive(self):
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(page=0)
        assert "page" in str(exc_info.value)

    def test_page_size_max_100(self):
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(page_size=101)
        assert "page_size" in str(exc_info.value)

    def test_page_size_min_1(self):
        with pytest.raises(ValidationError) as exc_info:
            PaginationParams(page_size=0)
        assert "page_size" in str(exc_info.value)


class TestEmployeeCreate:
    """Validates employee creation request body."""

    def test_valid_employee(self):
        emp = EmployeeCreate(badge_id="1234", name="John Doe")
        assert emp.badge_id == "1234"
        assert emp.name == "John Doe"

    def test_badge_id_must_be_4_to_6_digits(self):
        with pytest.raises(ValidationError) as exc_info:
            EmployeeCreate(badge_id="123", name="Test")
        assert "4-6 numeric digits" in str(exc_info.value)

    def test_badge_id_rejects_alpha(self):
        with pytest.raises(ValidationError) as exc_info:
            EmployeeCreate(badge_id="ABCD", name="Test")
        assert "4-6 numeric digits" in str(exc_info.value)

    def test_badge_id_accepts_6_digits(self):
        emp = EmployeeCreate(badge_id="123456", name="Test")
        assert emp.badge_id == "123456"

    def test_name_cannot_be_empty(self):
        with pytest.raises(ValidationError) as exc_info:
            EmployeeCreate(badge_id="1234", name="")
        assert "name" in str(exc_info.value).lower()

    def test_name_strips_whitespace(self):
        emp = EmployeeCreate(badge_id="1234", name="  Alice  ")
        assert emp.name == "Alice"


class TestAlertResolve:
    """Validates alert resolution request body."""

    def test_note_optional(self):
        payload = AlertResolve()
        assert payload.note is None

    def test_note_provided(self):
        payload = AlertResolve(note="Fixed the sensor")
        assert payload.note == "Fixed the sensor"

    def test_note_max_length(self):
        with pytest.raises(ValidationError):
            AlertResolve(note="x" * 501)


class TestLightZone:
    """Validates light zone coordinate constraints."""

    def test_valid_zone(self):
        zone = LightZone(x1=0.0, y1=0.0, x2=1.0, y2=1.0)
        assert zone.x2 == 1.0

    def test_x2_must_be_greater_than_x1(self):
        with pytest.raises(ValidationError) as exc_info:
            LightZone(x1=0.5, y1=0.0, x2=0.3, y2=1.0)
        assert "x2 must be greater than x1" in str(exc_info.value)

    def test_y2_must_be_greater_than_y1(self):
        with pytest.raises(ValidationError) as exc_info:
            LightZone(x1=0.0, y1=0.5, x2=1.0, y2=0.3)
        assert "y2 must be greater than y1" in str(exc_info.value)

    def test_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            LightZone(x1=-0.1, y1=0.0, x2=1.0, y2=1.0)


class TestCameraZonePayload:
    """Validates camera zone configuration request body."""

    def test_minimal_payload(self):
        payload = CameraZonePayload(id="M-01")
        assert payload.machine_id == "M-01"

    def test_machine_id_from_name(self):
        payload = CameraZonePayload(name="Machine-A")
        assert payload.machine_id == "Machine-A"

    def test_machine_id_from_machineName(self):
        payload = CameraZonePayload(machineName="CNC-02")
        assert payload.machine_id == "CNC-02"

    def test_light_zone_validated(self):
        payload = CameraZonePayload(
            id="M-01",
            lightZone={"x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 0.8}
        )
        assert payload.lightZone.x1 == 0.1
        assert payload.lightZone.y2 == 0.8

    def test_allows_extra_fields(self):
        payload = CameraZonePayload(id="M-01", detectionZone="custom", customField=42)
        # Extra fields should be allowed (wizard sends varied data)
        assert payload.id == "M-01"


class TestSettingsUpdate:
    """Validates settings update request body."""

    def test_accepts_arbitrary_keys(self):
        settings = SettingsUpdate(shift_hours=8, company_name="Acme")
        data = settings.model_dump(exclude_unset=True)
        assert "shift_hours" in data
        assert data["shift_hours"] == 8


class TestSetupInitRequest:
    """Validates initial setup request body."""

    def test_valid_setup(self):
        setup = SetupInitRequest(username="admin", password="secret123")
        assert setup.username == "admin"
        assert setup.company_name == "Cologic"

    def test_username_cannot_be_whitespace(self):
        with pytest.raises(ValidationError) as exc_info:
            SetupInitRequest(username="   ", password="secret123")
        assert "username" in str(exc_info.value).lower()

    def test_password_min_length(self):
        with pytest.raises(ValidationError) as exc_info:
            SetupInitRequest(username="admin", password="12345")
        assert "password" in str(exc_info.value).lower()

    def test_invalid_color_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            SetupInitRequest(
                username="admin", password="secret123", primary_color="not-a-color"
            )
        assert "color" in str(exc_info.value).lower()

    def test_valid_color_accepted(self):
        setup = SetupInitRequest(
            username="admin", password="secret123", primary_color="#FF0000"
        )
        assert setup.primary_color == "#FF0000"


class TestChatRequest:
    """Validates AI chat request body."""

    def test_valid_chat(self):
        chat = ChatRequest(messages=[{"role": "user", "content": "Hello"}])
        assert len(chat.messages) == 1
        assert chat.messages[0].role == "user"

    def test_empty_messages_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(messages=[])

    def test_invalid_role_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            ChatRequest(messages=[{"role": "invalid", "content": "Hello"}])
        assert "role" in str(exc_info.value).lower()

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            ChatRequest(messages=[{"role": "user", "content": ""}])


class TestPaginatedResponse:
    """Validates paginated response model."""

    def test_valid_response(self):
        resp = PaginatedResponse(
            data=[{"id": 1}], total=50, page=1, page_size=20, total_pages=3
        )
        assert resp.total == 50
        assert resp.total_pages == 3

    def test_negative_total_rejected(self):
        with pytest.raises(ValidationError):
            PaginatedResponse(
                data=[], total=-1, page=1, page_size=20, total_pages=0
            )
