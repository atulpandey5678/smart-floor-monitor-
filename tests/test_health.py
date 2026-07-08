"""Unit tests for health check endpoints (api/health.py).

Validates Requirements 14.1, 14.2, 14.3, 14.4:
- GET /health returns system status, DB connectivity, per-machine pipeline status
- GET /health returns 503 when DB unreachable or >50% pipelines in error
- GET /health/ready returns 200 only after migrations applied and at least one pipeline running
- Includes DB response time and per-pipeline last-frame timestamp
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.health import router, set_health_dependencies


@pytest.fixture
def app():
    """Create a minimal FastAPI app with only the health router mounted."""
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def client(app):
    """TestClient for the health-only app."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_health_deps():
    """Reset health module globals before each test."""
    set_health_dependencies(pipeline_orchestrator=None, async_db=None)
    # Also reset to None explicitly via module internals
    import api.health as h
    h._pipeline_orchestrator = None
    h._async_db = None
    yield
    h._pipeline_orchestrator = None
    h._async_db = None


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_200_when_db_reachable_no_pipelines(self, client):
        """Returns 200 healthy when DB is reachable and no pipelines exist."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(1,))
        set_health_dependencies(async_db=mock_db)

        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["database"]["reachable"] is True
        assert body["database"]["response_time_ms"] is not None
        assert body["pipelines"] == {}

    def test_health_503_when_db_unreachable(self, client):
        """Returns 503 degraded when DB probe fails."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(side_effect=Exception("connection lost"))
        set_health_dependencies(async_db=mock_db)

        resp = client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database"]["reachable"] is False

    def test_health_503_when_no_db_configured(self, client):
        """Returns 503 when no async_db is set at all."""
        resp = client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database"]["reachable"] is False
        assert body["database"]["response_time_ms"] is None

    def test_health_200_with_healthy_pipelines(self, client):
        """Returns 200 with per-machine pipeline statuses."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(1,))

        mock_orch = MagicMock()
        mock_orch.get_all_statuses.return_value = {
            "M-01": {"status": "running", "last_frame_time": 1700000000.0, "restart_count": 0, "last_error": None},
            "M-02": {"status": "running", "last_frame_time": 1700000001.0, "restart_count": 0, "last_error": None},
        }
        set_health_dependencies(pipeline_orchestrator=mock_orch, async_db=mock_db)

        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert "M-01" in body["pipelines"]
        assert body["pipelines"]["M-01"]["last_frame_time"] == 1700000000.0
        assert body["pipelines"]["M-02"]["status"] == "running"

    def test_health_503_when_majority_pipelines_in_error(self, client):
        """Returns 503 when >50% of pipelines are in error/failed state."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(1,))

        mock_orch = MagicMock()
        mock_orch.get_all_statuses.return_value = {
            "M-01": {"status": "error", "last_frame_time": 0, "restart_count": 2, "last_error": "crash"},
            "M-02": {"status": "failed", "last_frame_time": 0, "restart_count": 3, "last_error": "crash"},
            "M-03": {"status": "running", "last_frame_time": 1700000000.0, "restart_count": 0, "last_error": None},
        }
        set_health_dependencies(pipeline_orchestrator=mock_orch, async_db=mock_db)

        resp = client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"

    def test_health_200_when_exactly_half_in_error(self, client):
        """Returns 200 when exactly 50% are in error (not MORE than 50%)."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(1,))

        mock_orch = MagicMock()
        mock_orch.get_all_statuses.return_value = {
            "M-01": {"status": "error", "last_frame_time": 0, "restart_count": 2, "last_error": "crash"},
            "M-02": {"status": "running", "last_frame_time": 1700000000.0, "restart_count": 0, "last_error": None},
        }
        set_health_dependencies(pipeline_orchestrator=mock_orch, async_db=mock_db)

        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"

    def test_health_includes_db_response_time(self, client):
        """Response includes database response_time_ms metric."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(1,))
        set_health_dependencies(async_db=mock_db)

        resp = client.get("/health")
        body = resp.json()
        assert "response_time_ms" in body["database"]
        assert isinstance(body["database"]["response_time_ms"], float)
        assert body["database"]["response_time_ms"] >= 0


class TestReadinessEndpoint:
    """Tests for GET /health/ready."""

    def test_ready_503_when_no_db(self, client):
        """Returns 503 not ready when no DB is configured."""
        resp = client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        assert body["migrations_applied"] is False
        assert body["pipelines_running"] is False

    def test_ready_503_when_no_migrations(self, client):
        """Returns 503 when migrations table has 0 entries."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(0,))
        set_health_dependencies(async_db=mock_db)

        resp = client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        assert body["migrations_applied"] is False

    def test_ready_503_when_migrations_ok_but_no_pipelines(self, client):
        """Returns 503 when migrations applied but no pipeline running."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(3,))

        mock_orch = MagicMock()
        mock_orch.get_all_statuses.return_value = {
            "M-01": {"status": "stopped", "last_frame_time": 0, "restart_count": 0, "last_error": None},
        }
        set_health_dependencies(pipeline_orchestrator=mock_orch, async_db=mock_db)

        resp = client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        assert body["migrations_applied"] is True
        assert body["pipelines_running"] is False

    def test_ready_200_when_migrations_applied_and_pipeline_running(self, client):
        """Returns 200 when both conditions are met."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(return_value=(3,))

        mock_orch = MagicMock()
        mock_orch.get_all_statuses.return_value = {
            "M-01": {"status": "running", "last_frame_time": 1700000000.0, "restart_count": 0, "last_error": None},
        }
        set_health_dependencies(pipeline_orchestrator=mock_orch, async_db=mock_db)

        resp = client.get("/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ready"] is True
        assert body["migrations_applied"] is True
        assert body["pipelines_running"] is True

    def test_ready_503_when_migrations_check_fails(self, client):
        """Returns 503 when querying _migrations table throws."""
        mock_db = AsyncMock()
        mock_db.fetch_one = AsyncMock(side_effect=Exception("no such table"))
        set_health_dependencies(async_db=mock_db)

        resp = client.get("/health/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["ready"] is False
        assert body["migrations_applied"] is False
