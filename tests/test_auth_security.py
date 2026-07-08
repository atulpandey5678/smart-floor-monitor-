"""Unit tests for auth security upgrades (task 9.2).

Tests bcrypt hashing, password validation, login rate limiting,
CSRF protection, session cookie flags, and legacy hash migration.
"""

import time
import secrets
from unittest.mock import MagicMock, patch

import pytest
import bcrypt as bcrypt_lib

from api.auth import (
    _hash_password,
    _verify_password,
    _is_legacy_hash,
    _validate_password_length,
    _is_account_locked,
    _record_failed_attempt,
    _clear_failed_attempts,
    _failed_attempts,
    _generate_csrf_token,
    _should_set_secure,
    verify_csrf_token,
    MIN_PASSWORD_LENGTH,
    MAX_PASSWORD_LENGTH,
    MAX_FAILED_ATTEMPTS,
    LOCKOUT_WINDOW_SECONDS,
)
from fastapi import HTTPException


# ── Bcrypt hashing tests ──────────────────────────────────────

class TestBcryptHashing:
    """Tests for bcrypt password hashing with work factor 12."""

    def test_hash_produces_bcrypt_format(self):
        """Hash output starts with $2b$ indicating bcrypt."""
        result = _hash_password("testpassword")
        assert result.startswith("$2b$")

    def test_hash_uses_work_factor_12(self):
        """Hash uses rounds=12 as specified."""
        result = _hash_password("testpassword")
        # bcrypt format: $2b$12$...
        assert result.startswith("$2b$12$")

    def test_verify_correct_password(self):
        """Correct password verifies successfully."""
        hashed = _hash_password("mypassword123")
        assert _verify_password("mypassword123", hashed) is True

    def test_verify_wrong_password(self):
        """Wrong password fails verification."""
        hashed = _hash_password("mypassword123")
        assert _verify_password("wrongpassword", hashed) is False

    def test_different_hashes_for_same_password(self):
        """Each hash is unique due to random salt."""
        h1 = _hash_password("samepassword")
        h2 = _hash_password("samepassword")
        assert h1 != h2

    def test_hash_is_string(self):
        """Hash returns a string (not bytes)."""
        result = _hash_password("test")
        assert isinstance(result, str)


# ── Legacy hash migration tests ───────────────────────────────

class TestLegacyHashMigration:
    """Tests for backward-compatible SHA-256 hash verification."""

    def _make_legacy_hash(self, password: str) -> str:
        """Create a legacy salt:sha256hash string."""
        import hashlib
        salt = secrets.token_hex(8)
        h = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
        return f"{salt}:{h}"

    def test_is_legacy_hash_with_colon_format(self):
        """Legacy format detected by colon separator."""
        legacy = self._make_legacy_hash("test")
        assert _is_legacy_hash(legacy) is True

    def test_is_legacy_hash_with_bcrypt(self):
        """bcrypt format is not legacy."""
        bcrypt_hash = _hash_password("test")
        assert _is_legacy_hash(bcrypt_hash) is False

    def test_verify_legacy_hash(self):
        """Legacy SHA-256 hashes still verify correctly."""
        legacy = self._make_legacy_hash("oldpassword")
        assert _verify_password("oldpassword", legacy) is True

    def test_verify_legacy_hash_wrong_password(self):
        """Wrong password fails with legacy hash."""
        legacy = self._make_legacy_hash("oldpassword")
        assert _verify_password("wrongpassword", legacy) is False


# ── Password length validation tests ─────────────────────────

class TestPasswordValidation:
    """Tests for password length enforcement (8-128 chars)."""

    def test_password_too_short_raises(self):
        """Password under 8 chars raises HTTPException 400."""
        with pytest.raises(HTTPException) as exc_info:
            _validate_password_length("short")
        assert exc_info.value.status_code == 400
        assert "at least 8" in exc_info.value.detail

    def test_password_too_long_raises(self):
        """Password over 128 chars raises HTTPException 400."""
        long_pw = "a" * 129
        with pytest.raises(HTTPException) as exc_info:
            _validate_password_length(long_pw)
        assert exc_info.value.status_code == 400
        assert "not exceed 128" in exc_info.value.detail

    def test_password_minimum_length_accepted(self):
        """Password of exactly 8 chars passes validation."""
        _validate_password_length("a" * 8)  # Should not raise

    def test_password_maximum_length_accepted(self):
        """Password of exactly 128 chars passes validation."""
        _validate_password_length("a" * 128)  # Should not raise

    def test_password_normal_length_accepted(self):
        """Normal password length passes validation."""
        _validate_password_length("myStrongPassword123!")  # Should not raise


# ── Login rate limiting tests ─────────────────────────────────

class TestLoginRateLimiting:
    """Tests for account lockout after 5 failed attempts in 15 minutes."""

    def setup_method(self):
        """Clear failed attempts before each test."""
        _failed_attempts.clear()

    def test_account_not_locked_initially(self):
        """Fresh account is not locked."""
        assert _is_account_locked("testuser") is False

    def test_account_locks_after_max_attempts(self):
        """Account locks after 5 failed attempts."""
        for _ in range(MAX_FAILED_ATTEMPTS):
            _record_failed_attempt("testuser")
        assert _is_account_locked("testuser") is True

    def test_account_not_locked_under_max(self):
        """Account not locked with fewer than 5 attempts."""
        for _ in range(MAX_FAILED_ATTEMPTS - 1):
            _record_failed_attempt("testuser")
        assert _is_account_locked("testuser") is False

    def test_clear_failed_attempts_unlocks(self):
        """Clearing attempts unlocks the account."""
        for _ in range(MAX_FAILED_ATTEMPTS):
            _record_failed_attempt("testuser")
        _clear_failed_attempts("testuser")
        assert _is_account_locked("testuser") is False

    def test_old_attempts_expire(self):
        """Attempts older than 15 minutes are pruned."""
        # Record attempts in the past
        old_time = time.time() - LOCKOUT_WINDOW_SECONDS - 1
        _failed_attempts["testuser"] = [old_time] * MAX_FAILED_ATTEMPTS
        # Should not be locked since attempts are expired
        assert _is_account_locked("testuser") is False

    def test_different_users_independent(self):
        """Lockout is per-username, not global."""
        for _ in range(MAX_FAILED_ATTEMPTS):
            _record_failed_attempt("user1")
        assert _is_account_locked("user1") is True
        assert _is_account_locked("user2") is False


# ── CSRF token tests ──────────────────────────────────────────

class TestCSRFProtection:
    """Tests for CSRF double-submit cookie pattern."""

    def test_generate_csrf_token_format(self):
        """CSRF token is a 64-char hex string."""
        token = _generate_csrf_token()
        assert len(token) == 64
        assert all(c in "0123456789abcdef" for c in token)

    def test_verify_csrf_matching_tokens(self):
        """Matching cookie and header passes verification."""
        token = _generate_csrf_token()
        request = MagicMock()
        request.cookies = {"csrf_token": token}
        request.headers = {"X-CSRF-Token": token}
        request.url.path = "/api/v1/machines"
        # Should not raise
        verify_csrf_token(request)

    def test_verify_csrf_missing_cookie(self):
        """Missing CSRF cookie raises 403."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {"X-CSRF-Token": "sometoken"}
        request.url.path = "/api/v1/machines"
        with pytest.raises(HTTPException) as exc_info:
            verify_csrf_token(request)
        assert exc_info.value.status_code == 403

    def test_verify_csrf_missing_header(self):
        """Missing X-CSRF-Token header raises 403."""
        request = MagicMock()
        request.cookies = {"csrf_token": "sometoken"}
        request.headers = {}
        request.url.path = "/api/v1/machines"
        with pytest.raises(HTTPException) as exc_info:
            verify_csrf_token(request)
        assert exc_info.value.status_code == 403

    def test_verify_csrf_mismatched_tokens(self):
        """Mismatched cookie and header raises 403."""
        request = MagicMock()
        request.cookies = {"csrf_token": "token_a"}
        request.headers = {"X-CSRF-Token": "token_b"}
        request.url.path = "/api/v1/machines"
        with pytest.raises(HTTPException) as exc_info:
            verify_csrf_token(request)
        assert exc_info.value.status_code == 403

    def test_verify_csrf_skips_login(self):
        """CSRF check is skipped for /auth/login endpoint."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {}
        request.url.path = "/auth/login"
        # Should not raise even without tokens
        verify_csrf_token(request)

    def test_verify_csrf_skips_logout(self):
        """CSRF check is skipped for /auth/logout endpoint."""
        request = MagicMock()
        request.cookies = {}
        request.headers = {}
        request.url.path = "/auth/logout"
        # Should not raise
        verify_csrf_token(request)


# ── Secure cookie flag tests ──────────────────────────────────

class TestSecureCookieFlag:
    """Tests for Secure flag logic based on host."""

    def test_localhost_no_secure(self):
        """localhost requests don't get Secure flag."""
        request = MagicMock()
        request.headers = {"host": "localhost:8000"}
        assert _should_set_secure(request) is False

    def test_127_0_0_1_no_secure(self):
        """127.0.0.1 requests don't get Secure flag."""
        request = MagicMock()
        request.headers = {"host": "127.0.0.1:8000"}
        assert _should_set_secure(request) is False

    def test_production_host_gets_secure(self):
        """Production hosts get Secure flag."""
        request = MagicMock()
        request.headers = {"host": "app.example.com"}
        assert _should_set_secure(request) is True

    def test_ip_host_gets_secure(self):
        """Non-localhost IPs get Secure flag."""
        request = MagicMock()
        request.headers = {"host": "192.168.1.100:8000"}
        assert _should_set_secure(request) is True
