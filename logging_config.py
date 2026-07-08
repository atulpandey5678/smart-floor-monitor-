"""Structured JSON logging configuration using structlog.

Provides production-ready logging with:
- JSON output with ISO 8601 timestamps
- Rotating file handler (10 MB max, 5 retained)
- Sensitive data redaction (passwords, RTSP credentials, tokens)
- Per-module configurable log levels
- Thread-local machine_id context binding for pipeline threads

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5
"""

import logging
import logging.handlers
import re
import sys
from typing import Any

import structlog


# ── Sensitive Data Patterns ──────────────────────────────────────────────────

# Matches RTSP URLs with embedded credentials: rtsp://user:pass@host/path
_RTSP_CREDENTIAL_RE = re.compile(
    r"(rtsp://)[^:]+:[^@]+(@)", re.IGNORECASE
)

# Fields that must never appear in log output
_SENSITIVE_FIELD_NAMES = frozenset({
    "password",
    "password_hash",
    "token",
    "session_token",
    "secret_key",
    "rtsp_url",
    "rtsp_credentials",
})


# ── Sensitive Data Filter (structlog processor) ──────────────────────────────

def sensitive_data_filter(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that redacts sensitive data from log events.

    - Replaces credentials in RTSP URLs with '***'
    - Redacts fields named password, password_hash, token, etc.
    - Scans the event message string for embedded RTSP credentials
    """
    # Redact sensitive fields in the event dict
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_FIELD_NAMES:
            event_dict[key] = "***REDACTED***"

    # Redact RTSP credentials in string values
    for key, value in list(event_dict.items()):
        if isinstance(value, str) and "rtsp://" in value.lower():
            event_dict[key] = _RTSP_CREDENTIAL_RE.sub(r"\1***:***\2", value)

    # Also check the main event/message field
    event = event_dict.get("event", "")
    if isinstance(event, str) and "rtsp://" in event.lower():
        event_dict["event"] = _RTSP_CREDENTIAL_RE.sub(r"\1***:***\2", event)

    return event_dict


# ── Machine ID Context Binding ───────────────────────────────────────────────

def bind_machine_id(machine_id: str) -> None:
    """Bind machine_id to the current thread's structlog context.

    Call this when a CV pipeline thread starts. All subsequent log calls
    from that thread will automatically include the machine_id field.
    """
    structlog.contextvars.bind_contextvars(machine_id=machine_id)


def unbind_machine_id() -> None:
    """Remove machine_id from the current thread's structlog context."""
    structlog.contextvars.unbind_contextvars("machine_id")


def clear_contextvars() -> None:
    """Clear all thread-local structlog context variables."""
    structlog.contextvars.clear_contextvars()


# ── Setup Function ───────────────────────────────────────────────────────────

def setup_logging(
    log_level: str = "INFO",
    file_path: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
    module_levels: dict[str, str] | None = None,
) -> None:
    """Configure structured JSON logging with structlog and stdlib integration.

    Args:
        log_level: Default log level for all modules (e.g., "INFO", "DEBUG").
        file_path: Path to the rotating log file.
        max_bytes: Maximum size per log file before rotation (default 10 MB).
        backup_count: Number of rotated backup files to retain (default 5).
        module_levels: Optional dict mapping module names to log levels,
                       e.g. {"cv_pipeline": "DEBUG", "api": "INFO"}.
    """
    # ── Shared structlog processors (used by both structlog and stdlib) ───
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        sensitive_data_filter,
    ]

    # ── Configure structlog ──────────────────────────────────────────────
    structlog.configure(
        processors=[
            *shared_processors,
            # Prepare event dict for ProcessorFormatter
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ── Configure stdlib logging (for libraries and routing) ─────────────
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove any pre-existing handlers to avoid duplicate output
    root_logger.handlers.clear()

    # JSON formatter for file output (production)
    json_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    # Console formatter (dev-friendly when TTY, JSON otherwise)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer()
            if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        foreign_pre_chain=shared_processors,
    )

    # ── Rotating file handler ────────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        filename=file_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(json_formatter)
    root_logger.addHandler(file_handler)

    # ── Console handler ──────────────────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # ── Per-module log levels ────────────────────────────────────────────
    if module_levels:
        for module_name, level in module_levels.items():
            module_logger = logging.getLogger(module_name)
            module_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
