"""Tests for the data retention cleanup task (engine/data_retention.py).

Requirements tested: 12.1, 12.2, 12.3, 12.4
"""

import json
import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.data_retention import (
    DataRetentionTask,
    get_cleanup_hour_minute,
    schedule_retention_job,
)


# ── Helper fixtures ────────────────────────────────────────────


class FakeCursor:
    """Mimics a DB cursor with rowcount."""

    def __init__(self, rowcount=0):
        self.rowcount = rowcount


class FakeRow(dict):
    """Dict subclass that supports both key-access and iteration for dict()."""
    pass


@pytest.fixture
def mock_repo():
    """Create a mock Repository with async DB methods."""
    repo = MagicMock()
    repo.db = MagicMock()
    repo.db.fetch_all = AsyncMock(return_value=[])
    repo.db.execute = AsyncMock(return_value=FakeCursor(0))
    return repo


@pytest.fixture
def backup_dir(tmp_path):
    """Create a temporary backup directory."""
    return str(tmp_path / "backups")


@pytest.fixture
def task(mock_repo, backup_dir):
    """Create a DataRetentionTask with mock repo and temp backup dir."""
    return DataRetentionTask(mock_repo, backup_dir)


# ── get_cleanup_hour_minute tests ─────────────────────────────


class TestGetCleanupHourMinute:
    def test_valid_time(self):
        assert get_cleanup_hour_minute("02:00") == (2, 0)

    def test_valid_time_afternoon(self):
        assert get_cleanup_hour_minute("14:30") == (14, 30)

    def test_valid_time_with_whitespace(self):
        assert get_cleanup_hour_minute("  03:15  ") == (3, 15)

    def test_invalid_format_returns_default(self):
        assert get_cleanup_hour_minute("invalid") == (2, 0)

    def test_empty_string_returns_default(self):
        assert get_cleanup_hour_minute("") == (2, 0)


# ── DataRetentionTask tests ───────────────────────────────────


class TestDataRetentionTask:
    @pytest.mark.asyncio
    async def test_no_records_to_clean(self, task, mock_repo):
        """When no records are older than the cutoff, nothing is deleted."""
        mock_repo.db.fetch_all = AsyncMock(return_value=[])

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "retention_days"): 90,
                    ("retention", "archive_enabled"): True,
                    ("retention", "archive_time"): "02:00",
                }.get((section, key), default)
            )
            result = await task.run_cleanup()

        assert result["sessions_deleted"] == 0
        assert result["alerts_deleted"] == 0
        assert result["backup_file"] is None

    @pytest.mark.asyncio
    async def test_cleanup_deletes_old_sessions(self, task, mock_repo, backup_dir):
        """Sessions older than retention period are deleted after backup."""
        old_session = FakeRow({
            "id": 1,
            "badge_id": "1234",
            "machine_id": "M-01",
            "start_time": "2024-01-01T08:00:00",
            "end_time": "2024-01-01T16:00:00",
            "active_duration_seconds": 28800.0,
            "state": "CLOSED",
            "close_reason": "shift_end",
            "created_at": "2024-01-01T08:00:00",
        })

        # First call returns sessions, second returns empty alerts
        mock_repo.db.fetch_all = AsyncMock(side_effect=[
            [old_session],  # sessions
            [],             # alerts
        ])
        # Delete returns cursor with rowcount
        mock_repo.db.execute = AsyncMock(side_effect=[
            FakeCursor(1),  # sessions deleted
            FakeCursor(0),  # alerts deleted
        ])

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "retention_days"): 90,
                    ("retention", "archive_enabled"): True,
                    ("retention", "archive_time"): "02:00",
                }.get((section, key), default)
            )
            result = await task.run_cleanup()

        assert result["sessions_deleted"] == 1
        assert result["alerts_deleted"] == 0
        assert result["backup_file"] is not None
        assert os.path.exists(result["backup_file"])

        # Verify backup file content
        with open(result["backup_file"], "r") as f:
            backup_data = json.load(f)
        assert backup_data["sessions_count"] == 1
        assert backup_data["sessions"][0]["badge_id"] == "1234"

    @pytest.mark.asyncio
    async def test_cleanup_retains_alerts_full_period(self, task, mock_repo, backup_dir):
        """Alerts older than retention are also deleted (retained for full period, Req 12.3)."""
        old_alert = FakeRow({
            "id": 10,
            "badge_id": "5678",
            "machine_id": "M-02",
            "alert_type": "static_worker",
            "message": "Worker static for 3 minutes",
            "resolved": 1,
            "root_cause": "break",
            "created_at": "2024-01-01T10:00:00",
        })

        mock_repo.db.fetch_all = AsyncMock(side_effect=[
            [],            # no old sessions
            [old_alert],   # old alerts
        ])
        mock_repo.db.execute = AsyncMock(side_effect=[
            FakeCursor(0),  # sessions deleted
            FakeCursor(1),  # alerts deleted
        ])

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "retention_days"): 90,
                    ("retention", "archive_enabled"): True,
                    ("retention", "archive_time"): "02:00",
                }.get((section, key), default)
            )
            result = await task.run_cleanup()

        assert result["alerts_deleted"] == 1
        assert result["backup_file"] is not None

    @pytest.mark.asyncio
    async def test_archive_disabled_skips_export(self, task, mock_repo, backup_dir):
        """When archive_enabled is False, records are deleted without backup."""
        old_session = FakeRow({
            "id": 1,
            "badge_id": "1234",
            "machine_id": "M-01",
            "start_time": "2024-01-01T08:00:00",
            "end_time": "2024-01-01T16:00:00",
            "active_duration_seconds": 28800.0,
            "state": "CLOSED",
            "close_reason": "shift_end",
            "created_at": "2024-01-01T08:00:00",
        })

        mock_repo.db.fetch_all = AsyncMock(side_effect=[
            [old_session],
            [],
        ])
        mock_repo.db.execute = AsyncMock(side_effect=[
            FakeCursor(1),
            FakeCursor(0),
        ])

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "retention_days"): 90,
                    ("retention", "archive_enabled"): False,
                    ("retention", "archive_time"): "02:00",
                }.get((section, key), default)
            )
            result = await task.run_cleanup()

        assert result["sessions_deleted"] == 1
        assert result["backup_file"] is None

    @pytest.mark.asyncio
    async def test_configurable_retention_days(self, task, mock_repo):
        """Retention days setting is used for cutoff calculation."""
        mock_repo.db.fetch_all = AsyncMock(return_value=[])

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "retention_days"): 30,
                    ("retention", "archive_enabled"): True,
                    ("retention", "archive_time"): "02:00",
                }.get((section, key), default)
            )
            result = await task.run_cleanup()

        # Verify the cutoff is roughly 30 days ago
        cutoff = datetime.fromisoformat(result["cutoff_date"])
        expected_cutoff = datetime.now() - timedelta(days=30)
        assert abs((cutoff - expected_cutoff).total_seconds()) < 5

    @pytest.mark.asyncio
    async def test_backup_file_contains_metadata(self, task, mock_repo, backup_dir):
        """Backup file includes export timestamp, cutoff, and record counts."""
        old_session = FakeRow({
            "id": 1,
            "badge_id": "ABCD",
            "machine_id": "M-01",
            "start_time": "2024-01-15T09:00:00",
            "end_time": "2024-01-15T17:00:00",
            "active_duration_seconds": 28000.0,
            "state": "CLOSED",
            "close_reason": "normal",
            "created_at": "2024-01-15T09:00:00",
        })

        mock_repo.db.fetch_all = AsyncMock(side_effect=[
            [old_session],
            [],
        ])
        mock_repo.db.execute = AsyncMock(side_effect=[
            FakeCursor(1),
            FakeCursor(0),
        ])

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "retention_days"): 90,
                    ("retention", "archive_enabled"): True,
                    ("retention", "archive_time"): "02:00",
                }.get((section, key), default)
            )
            result = await task.run_cleanup()

        with open(result["backup_file"], "r") as f:
            data = json.load(f)

        assert "exported_at" in data
        assert "cutoff_date" in data
        assert data["sessions_count"] == 1
        assert data["alerts_count"] == 0


# ── schedule_retention_job tests ──────────────────────────────


class TestScheduleRetentionJob:
    def test_schedules_job_with_correct_time(self, mock_repo):
        """Job is scheduled at the configured archive_time."""
        scheduler = MagicMock()

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "archive_time"): "03:30",
                }.get((section, key), default)
            )
            task = schedule_retention_job(scheduler, mock_repo)

        scheduler.add_job.assert_called_once()
        call_kwargs = scheduler.add_job.call_args[1]
        assert call_kwargs["trigger"] == "cron"
        assert call_kwargs["hour"] == 3
        assert call_kwargs["minute"] == 30
        assert call_kwargs["id"] == "data_retention_cleanup"

    def test_schedules_job_default_time(self, mock_repo):
        """When archive_time is default '02:00', job runs at 2 AM."""
        scheduler = MagicMock()

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: {
                    ("retention", "archive_time"): "02:00",
                }.get((section, key), default)
            )
            task = schedule_retention_job(scheduler, mock_repo)

        call_kwargs = scheduler.add_job.call_args[1]
        assert call_kwargs["hour"] == 2
        assert call_kwargs["minute"] == 0

    def test_returns_task_instance(self, mock_repo):
        """schedule_retention_job returns the DataRetentionTask instance."""
        scheduler = MagicMock()

        with patch("engine.data_retention.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                get=lambda section, key, default=None: "02:00"
            )
            task = schedule_retention_job(scheduler, mock_repo)

        assert isinstance(task, DataRetentionTask)
