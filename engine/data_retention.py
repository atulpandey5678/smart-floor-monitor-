"""Data Retention — Scheduled cleanup of old session and alert data.

Runs daily at a configurable time (default 02:00) and removes session
records older than the configured retention period (default 90 days).
Alerts are retained for the full retention period regardless of resolution.
Deleted records are exported to a dated JSON backup file before removal.

Requirements: 12.1, 12.2, 12.3, 12.4
"""

import json
import structlog
import os
from datetime import datetime, timedelta
from typing import List, Optional

from db.repository import Repository
from engine.settings_manager import get_settings

logger = structlog.get_logger(__name__)

# Default backup directory (relative to project root)
DEFAULT_BACKUP_DIR = "backups"


class DataRetentionTask:
    """Handles scheduled cleanup of old session and event data."""

    def __init__(self, repo: Repository, backup_dir: Optional[str] = None):
        self.repo = repo
        self.backup_dir = backup_dir or DEFAULT_BACKUP_DIR

    def _get_retention_settings(self) -> dict:
        """Read retention settings from SettingsManager."""
        settings = get_settings()
        return {
            "retention_days": settings.get("retention", "retention_days", 90),
            "archive_enabled": settings.get("retention", "archive_enabled", True),
            "archive_time": settings.get("retention", "archive_time", "02:00"),
        }

    def _ensure_backup_dir(self) -> str:
        """Create backup directory if it doesn't exist. Returns absolute path."""
        abs_path = os.path.abspath(self.backup_dir)
        os.makedirs(abs_path, exist_ok=True)
        return abs_path

    async def _fetch_old_sessions(self, cutoff: datetime) -> List[dict]:
        """Fetch session records older than the cutoff date."""
        rows = await self.repo.db.fetch_all(
            """SELECT id, badge_id, machine_id, start_time, end_time,
                      active_duration_seconds, state, close_reason, created_at
               FROM sessions
               WHERE start_time < ?
               ORDER BY start_time ASC""",
            (cutoff.isoformat(),),
        )
        return [dict(r) for r in rows]

    async def _fetch_old_alerts(self, cutoff: datetime) -> List[dict]:
        """Fetch alert records older than the cutoff date.

        Alerts are retained for the full retention period (Requirement 12.3),
        so this fetches alerts older than the cutoff for backup purposes only
        when sessions are being deleted that reference them.
        """
        rows = await self.repo.db.fetch_all(
            """SELECT id, badge_id, machine_id, alert_type, message,
                      resolved, root_cause, created_at
               FROM alerts
               WHERE created_at < ?
               ORDER BY created_at ASC""",
            (cutoff.isoformat(),),
        )
        return [dict(r) for r in rows]

    def _export_to_json(self, sessions: List[dict], alerts: List[dict], cutoff: datetime) -> str:
        """Export records to a dated JSON backup file. Returns the file path."""
        backup_dir = self._ensure_backup_dir()
        date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        filename = f"retention_cleanup_{date_str}.json"
        filepath = os.path.join(backup_dir, filename)

        backup_data = {
            "exported_at": datetime.now().isoformat(),
            "cutoff_date": cutoff.isoformat(),
            "sessions_count": len(sessions),
            "alerts_count": len(alerts),
            "sessions": sessions,
            "alerts": alerts,
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=2, default=str)

        logger.info("Exported %d sessions and %d alerts to %s",
                    len(sessions), len(alerts), filepath)
        return filepath

    async def _delete_old_sessions(self, cutoff: datetime) -> int:
        """Delete session records older than the cutoff. Returns count deleted."""
        cursor = await self.repo.db.execute(
            "DELETE FROM sessions WHERE start_time < ?",
            (cutoff.isoformat(),),
        )
        return cursor.rowcount

    async def _delete_old_alerts(self, cutoff: datetime) -> int:
        """Delete alert records older than the cutoff. Returns count deleted."""
        cursor = await self.repo.db.execute(
            "DELETE FROM alerts WHERE created_at < ?",
            (cutoff.isoformat(),),
        )
        return cursor.rowcount

    async def run_cleanup(self) -> dict:
        """Execute the data retention cleanup.

        Steps:
        1. Read retention settings
        2. Calculate cutoff date
        3. Fetch records to be deleted
        4. Export to JSON backup (if archive_enabled)
        5. Delete old sessions
        6. Delete old alerts (retained for full retention period per Req 12.3)
        7. Log results

        Returns a summary dict with counts and time range.
        """
        start_time = datetime.now()
        retention = self._get_retention_settings()
        retention_days = retention["retention_days"]
        archive_enabled = retention["archive_enabled"]

        cutoff = datetime.now() - timedelta(days=retention_days)

        logger.info("Data retention cleanup started: retention_days=%d, cutoff=%s",
                    retention_days, cutoff.isoformat())

        # Fetch records that will be deleted
        old_sessions = await self._fetch_old_sessions(cutoff)
        old_alerts = await self._fetch_old_alerts(cutoff)

        if not old_sessions and not old_alerts:
            logger.info("Data retention cleanup: no records older than %s to remove", cutoff.isoformat())
            return {
                "sessions_deleted": 0,
                "alerts_deleted": 0,
                "cutoff_date": cutoff.isoformat(),
                "backup_file": None,
                "duration_seconds": (datetime.now() - start_time).total_seconds(),
            }

        # Export to JSON backup before deletion (Requirement 12.4)
        backup_file = None
        if archive_enabled:
            backup_file = self._export_to_json(old_sessions, old_alerts, cutoff)

        # Delete old sessions (Requirement 12.1)
        sessions_deleted = await self._delete_old_sessions(cutoff)

        # Delete old alerts that exceed retention period (Requirement 12.3)
        # Alerts are retained for the FULL retention period, same as sessions
        alerts_deleted = await self._delete_old_alerts(cutoff)

        duration = (datetime.now() - start_time).total_seconds()

        # Log results (Requirement 12.2)
        logger.info(
            "Data retention cleanup completed: "
            "sessions_deleted=%d, alerts_deleted=%d, "
            "time_range=before %s, duration=%.2fs, backup=%s",
            sessions_deleted, alerts_deleted,
            cutoff.isoformat(), duration,
            backup_file or "disabled",
        )

        return {
            "sessions_deleted": sessions_deleted,
            "alerts_deleted": alerts_deleted,
            "cutoff_date": cutoff.isoformat(),
            "backup_file": backup_file,
            "duration_seconds": duration,
        }


def get_cleanup_hour_minute(archive_time: str) -> tuple:
    """Parse 'HH:MM' string into (hour, minute) tuple."""
    try:
        parts = archive_time.strip().split(":")
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        logger.warning("Invalid archive_time '%s', using default 02:00", archive_time)
        return 2, 0


def schedule_retention_job(scheduler, repo: Repository, backup_dir: Optional[str] = None):
    """Register the data retention cleanup job with APScheduler.

    Reads the configured time from settings and schedules a daily cron job.
    """
    settings = get_settings()
    archive_time = settings.get("retention", "archive_time", "02:00")
    hour, minute = get_cleanup_hour_minute(archive_time)

    task = DataRetentionTask(repo, backup_dir)

    scheduler.add_job(
        task.run_cleanup,
        trigger="cron",
        hour=hour,
        minute=minute,
        id="data_retention_cleanup",
        replace_existing=True,
        name="Daily data retention cleanup",
    )

    logger.info("Scheduled data retention cleanup at %02d:%02d daily", hour, minute)
    return task
