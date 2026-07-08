"""Tests for global rate limiting middleware (Requirement 9.1, 9.4).

Verifies:
- 100 requests/minute per client IP on all REST endpoints
- HTTP 429 returned with Retry-After header when limit exceeded
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def test_app():
    """Create a minimal FastAPI app with the rate limiter for testing."""
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded

    # Use a low limit for testing (3/minute instead of 100/minute)
    limiter = Limiter(key_func=get_remote_address, default_limits=["3/minute"])

    app = FastAPI()
    app.state.limiter = limiter

    def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        """Custom handler that always includes Retry-After header."""
        return JSONResponse(
            status_code=429,
            content={"error": f"Rate limit exceeded: {exc.detail}"},
            headers={"Retry-After": "60"},
        )

    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    @app.get("/api/test")
    @limiter.limit("3/minute")
    async def test_endpoint(request: Request):
        return {"status": "ok"}

    @app.get("/api/v1/machines")
    @limiter.limit("3/minute")
    async def machines_endpoint(request: Request):
        return {"machines": []}

    return app


@pytest.fixture
def client(test_app):
    """Create a test client."""
    return TestClient(test_app)


class TestGlobalRateLimiting:
    """Test global rate limiting behavior."""

    def test_requests_within_limit_succeed(self, client):
        """Requests within the rate limit should return 200."""
        for _ in range(3):
            response = client.get("/api/test")
            assert response.status_code == 200

    def test_exceeding_limit_returns_429(self, client):
        """Requests exceeding the rate limit should return HTTP 429."""
        # Use up the limit
        for _ in range(3):
            response = client.get("/api/test")
            assert response.status_code == 200

        # Next request should be rate limited
        response = client.get("/api/test")
        assert response.status_code == 429

    def test_429_includes_retry_after_header(self, client):
        """HTTP 429 responses must include a Retry-After header (Requirement 9.4)."""
        # Exhaust the limit
        for _ in range(3):
            client.get("/api/test")

        response = client.get("/api/test")
        assert response.status_code == 429
        # Verify Retry-After header is present
        assert "Retry-After" in response.headers
        # Verify response body has error info
        body = response.json()
        assert "error" in body

    def test_rate_limit_applies_per_endpoint(self, client):
        """Rate limit is per client IP, shared across endpoints with default_limits."""
        # Note: with slowapi default_limits, the limit is per-endpoint
        # Each endpoint has its own 3/minute limit in this test config
        for _ in range(3):
            response = client.get("/api/test")
            assert response.status_code == 200

        # This endpoint has its own limit counter
        for _ in range(3):
            response = client.get("/api/v1/machines")
            assert response.status_code == 200


class TestRateLimiterConfiguration:
    """Test that rate limiter is properly configured in the production app."""

    def test_limiter_uses_remote_address_key(self):
        """The limiter should use client IP as the rate limit key."""
        from api.server import limiter
        from slowapi.util import get_remote_address

        assert limiter._key_func == get_remote_address

    def test_limiter_default_limit_is_100_per_minute(self):
        """The default rate limit should be 100 requests per minute."""
        from api.server import limiter

        # slowapi stores default limits as LimitGroup objects containing Limit items
        limit_items = [item for lg in limiter._default_limits for item in lg]
        limit_strs = [str(item.limit) for item in limit_items]
        assert any("100" in s and "minute" in s for s in limit_strs)

    def test_app_has_limiter_in_state(self):
        """The FastAPI app should have limiter attached to its state."""
        from api.server import app

        assert hasattr(app.state, "limiter")
        assert app.state.limiter is not None

    def test_rate_limit_exceeded_handler_registered(self):
        """The app should have a handler for RateLimitExceeded exceptions."""
        from api.server import app
        from slowapi.errors import RateLimitExceeded

        # FastAPI stores exception handlers in its exception_handlers dict
        assert RateLimitExceeded in app.exception_handlers
