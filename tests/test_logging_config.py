"""Tests for structured logging configuration module.

Validates:
- JSON output format with required fields
- Sensitive data redaction (passwords, RTSP credentials, tokens)
- Machine_id context binding
- Rotating file handler configuration
- Per-module log level configuration
"""

import json
import logging
import logging.handlers
import os
import tempfile
from unittest.mock import patch

import pytest
import structlog

from logging_config import (
    bind_machine_id,
    clear_contextvars,
    sensitive_data_filter,
    setup_logging,
    unbind_machine_id,
)


@pytest.fixture(autouse=True)
def _reset_logging():
    """Reset logging state before and after each test."""
    clear_contextvars()
    yield
    # Reset after test — close all handlers before temp dir cleanup
    clear_contextvars()
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)


def _close_handlers():
    """Close all logging handlers (needed on Windows before temp file cleanup)."""
    root = logging.getLogger()
    for handler in root.handlers[:]:
        handler.close()
        root.removeHandler(handler)


class TestSensitiveDataFilter:
    """Tests for the sensitive_data_filter structlog processor."""

    def test_redacts_password_field(self):
        event_dict = {"event": "user login", "password": "secret123"}
        result = sensitive_data_filter(None, "info", event_dict)
        assert result["password"] == "***REDACTED***"

    def test_redacts_password_hash_field(self):
        event_dict = {"event": "user created", "password_hash": "$2b$12$abc"}
        result = sensitive_data_filter(None, "info", event_dict)
        assert result["password_hash"] == "***REDACTED***"

    def test_redacts_token_field(self):
        event_dict = {"event": "auth check", "token": "eyJhbGciOiJIUzI1NiJ9.abc"}
        result = sensitive_data_filter(None, "info", event_dict)
        assert result["token"] == "***REDACTED***"

    def test_redacts_session_token_field(self):
        event_dict = {"event": "session", "session_token": "tok_abc123"}
        result = sensitive_data_filter(None, "info", event_dict)
        assert result["session_token"] == "***REDACTED***"

    def test_redacts_rtsp_credentials_in_value(self):
        event_dict = {
            "event": "pipeline start",
            "url": "rtsp://admin:password123@192.168.1.50:554/stream",
        }
        result = sensitive_data_filter(None, "info", event_dict)
        assert "password123" not in result["url"]
        assert "admin" not in result["url"]
        assert "***:***" in result["url"]
        assert "192.168.1.50:554/stream" in result["url"]

    def test_redacts_rtsp_credentials_in_event_message(self):
        event_dict = {
            "event": "Connecting to rtsp://user:pass@10.0.0.1/cam1",
        }
        result = sensitive_data_filter(None, "info", event_dict)
        assert "pass" not in result["event"]
        assert "user" not in result["event"]
        assert "***:***" in result["event"]

    def test_preserves_non_sensitive_fields(self):
        event_dict = {
            "event": "pipeline running",
            "machine_id": "M-01",
            "fps": 30,
        }
        result = sensitive_data_filter(None, "info", event_dict)
        assert result["machine_id"] == "M-01"
        assert result["fps"] == 30
        assert result["event"] == "pipeline running"

    def test_handles_non_string_values(self):
        event_dict = {"event": "stats", "count": 42, "items": [1, 2, 3]}
        result = sensitive_data_filter(None, "info", event_dict)
        assert result["count"] == 42
        assert result["items"] == [1, 2, 3]

    def test_case_insensitive_field_matching(self):
        event_dict = {"event": "auth", "Password": "secret"}
        result = sensitive_data_filter(None, "info", event_dict)
        assert result["Password"] == "***REDACTED***"


class TestMachineIdBinding:
    """Tests for machine_id context variable binding."""

    def test_bind_machine_id_sets_context(self):
        bind_machine_id("M-03")
        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("machine_id") == "M-03"

    def test_unbind_machine_id_removes_context(self):
        bind_machine_id("M-05")
        unbind_machine_id()
        ctx = structlog.contextvars.get_contextvars()
        assert "machine_id" not in ctx

    def test_clear_contextvars_removes_all(self):
        bind_machine_id("M-01")
        structlog.contextvars.bind_contextvars(extra="value")
        clear_contextvars()
        ctx = structlog.contextvars.get_contextvars()
        assert ctx == {}


class TestSetupLogging:
    """Tests for the setup_logging function."""

    def test_creates_rotating_file_handler(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(file_path=log_file)

            root = logging.getLogger()
            rotating_handlers = [
                h
                for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(rotating_handlers) == 1
            handler = rotating_handlers[0]
            assert handler.maxBytes == 10 * 1024 * 1024
            assert handler.backupCount == 5
            _close_handlers()

    def test_creates_console_handler(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(file_path=log_file)

            root = logging.getLogger()
            stream_handlers = [
                h
                for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            assert len(stream_handlers) == 1
            _close_handlers()

    def test_sets_root_log_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(log_level="DEBUG", file_path=log_file)

            root = logging.getLogger()
            assert root.level == logging.DEBUG
            _close_handlers()

    def test_configures_per_module_levels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(
                file_path=log_file,
                module_levels={"cv_pipeline": "DEBUG", "api": "WARNING"},
            )

            cv_logger = logging.getLogger("cv_pipeline")
            api_logger = logging.getLogger("api")
            assert cv_logger.level == logging.DEBUG
            assert api_logger.level == logging.WARNING
            _close_handlers()

    def test_custom_rotation_parameters(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(
                file_path=log_file,
                max_bytes=5 * 1024 * 1024,
                backup_count=3,
            )

            root = logging.getLogger()
            rotating_handlers = [
                h
                for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            ]
            handler = rotating_handlers[0]
            assert handler.maxBytes == 5 * 1024 * 1024
            assert handler.backupCount == 3
            _close_handlers()

    def test_json_output_to_file(self):
        """Verify that log entries written to file are valid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(file_path=log_file)

            logger = logging.getLogger("test_module")
            logger.setLevel(logging.INFO)
            logger.info("test message")

            # Flush handlers
            for handler in logging.getLogger().handlers:
                handler.flush()

            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read().strip()

            assert content, "Log file should not be empty"
            # Parse as JSON to confirm valid format
            entry = json.loads(content)
            assert "event" in entry
            assert "level" in entry
            assert "timestamp" in entry
            assert "logger" in entry
            assert entry["event"] == "test message"
            assert entry["level"] == "info"
            _close_handlers()

    def test_json_includes_machine_id_from_context(self):
        """Verify machine_id appears in JSON output when bound."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(file_path=log_file)

            bind_machine_id("M-07")
            logger = logging.getLogger("cv_pipeline.detector")
            logger.setLevel(logging.INFO)
            logger.info("frame processed")

            for handler in logging.getLogger().handlers:
                handler.flush()

            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read().strip()

            entry = json.loads(content)
            assert entry.get("machine_id") == "M-07"
            _close_handlers()

    def test_sensitive_data_redacted_in_file_output(self):
        """Verify sensitive fields are redacted in the JSON file output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = os.path.join(tmpdir, "test.log")
            setup_logging(file_path=log_file)

            logger = logging.getLogger("test_security")
            logger.setLevel(logging.INFO)
            logger.info(
                "Connecting to rtsp://admin:secret@192.168.1.1/stream"
            )

            for handler in logging.getLogger().handlers:
                handler.flush()

            with open(log_file, "r", encoding="utf-8") as f:
                content = f.read().strip()

            assert "secret" not in content
            assert "admin" not in content
            assert "***:***" in content
            _close_handlers()
