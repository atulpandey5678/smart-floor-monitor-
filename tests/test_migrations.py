"""Tests for db.migrations — MigrationRunner."""

import hashlib
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.migrations import MigrationError, MigrationRunner


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary SQLite database file."""
    return str(tmp_path / "test.db")


@pytest.fixture
def migrations_dir(tmp_path):
    """Return a temporary migrations directory."""
    d = tmp_path / "migrations"
    d.mkdir()
    return d


def _write_migration(migrations_dir: Path, filename: str, sql: str) -> Path:
    """Helper to write a migration SQL file."""
    p = migrations_dir / filename
    p.write_text(sql, encoding="utf-8")
    return p


class TestMigrationRunner:
    """Test suite for MigrationRunner."""

    def test_creates_migrations_table(self, tmp_db, migrations_dir):
        runner = MigrationRunner(tmp_db, migrations_dir)
        runner.run()

        # Verify _migrations table exists
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
        )
        assert cursor.fetchone() is not None
        conn.close()
        runner.close()

    def test_applies_single_migration(self, tmp_db, migrations_dir):
        _write_migration(
            migrations_dir,
            "001_create_users.sql",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
        )
        runner = MigrationRunner(tmp_db, migrations_dir)
        results = runner.run()

        assert len(results) == 1
        assert results[0]["version"] == 1
        assert results[0]["name"] == "create_users"

        # Verify the table was created
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
        assert cursor.fetchone() is not None
        conn.close()
        runner.close()

    def test_applies_multiple_migrations_in_order(self, tmp_db, migrations_dir):
        _write_migration(
            migrations_dir,
            "001_create_users.sql",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
        )
        _write_migration(
            migrations_dir,
            "002_create_orders.sql",
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER);",
        )
        _write_migration(
            migrations_dir,
            "003_add_email.sql",
            "ALTER TABLE users ADD COLUMN email TEXT;",
        )

        runner = MigrationRunner(tmp_db, migrations_dir)
        results = runner.run()

        assert len(results) == 3
        assert [r["version"] for r in results] == [1, 2, 3]
        runner.close()

    def test_skips_already_applied_migrations(self, tmp_db, migrations_dir):
        _write_migration(
            migrations_dir,
            "001_create_users.sql",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
        )

        runner = MigrationRunner(tmp_db, migrations_dir)
        results_first = runner.run()
        assert len(results_first) == 1

        # Add a second migration and run again
        _write_migration(
            migrations_dir,
            "002_create_orders.sql",
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, user_id INTEGER);",
        )
        results_second = runner.run()
        assert len(results_second) == 1
        assert results_second[0]["version"] == 2
        runner.close()

    def test_failed_migration_raises_and_rolls_back(self, tmp_db, migrations_dir):
        _write_migration(
            migrations_dir,
            "001_create_users.sql",
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);",
        )
        _write_migration(
            migrations_dir,
            "002_bad_migration.sql",
            "CREATE TABLE invalid (id INTEGER PRIMARY KEY;\n-- syntax error above",
        )

        runner = MigrationRunner(tmp_db, migrations_dir)
        with pytest.raises(MigrationError) as exc_info:
            runner.run()

        assert exc_info.value.version == 2
        assert exc_info.value.name == "bad_migration"

        # First migration should still be applied
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute("SELECT version FROM _migrations")
        versions = [row[0] for row in cursor.fetchall()]
        assert 1 in versions
        assert 2 not in versions
        conn.close()
        runner.close()

    def test_checksum_is_sha256(self, tmp_db, migrations_dir):
        sql_content = "CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT);"
        path = _write_migration(migrations_dir, "001_items.sql", sql_content)

        expected_checksum = hashlib.sha256(sql_content.encode("utf-8")).hexdigest()

        runner = MigrationRunner(tmp_db, migrations_dir)
        results = runner.run()

        assert results[0]["checksum"] == expected_checksum
        runner.close()

    def test_get_applied_migrations(self, tmp_db, migrations_dir):
        _write_migration(
            migrations_dir,
            "001_init.sql",
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY);",
        )
        _write_migration(
            migrations_dir,
            "002_more.sql",
            "CREATE TABLE t2 (id INTEGER PRIMARY KEY);",
        )

        runner = MigrationRunner(tmp_db, migrations_dir)
        runner.run()

        applied = runner.get_applied_migrations()
        assert len(applied) == 2
        assert applied[0]["version"] == 1
        assert applied[1]["version"] == 2
        assert applied[0]["applied_at"] is not None
        runner.close()

    def test_no_pending_migrations(self, tmp_db, migrations_dir):
        runner = MigrationRunner(tmp_db, migrations_dir)
        results = runner.run()
        assert results == []
        runner.close()

    def test_missing_migrations_directory(self, tmp_db, tmp_path):
        nonexistent = tmp_path / "nonexistent_dir"
        runner = MigrationRunner(tmp_db, nonexistent)
        results = runner.run()
        assert results == []
        runner.close()

    def test_ignores_non_sql_files(self, tmp_db, migrations_dir):
        _write_migration(
            migrations_dir,
            "001_init.sql",
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY);",
        )
        # Write a non-matching file
        (migrations_dir / "README.md").write_text("Not a migration")
        (migrations_dir / "notes.txt").write_text("Just notes")

        runner = MigrationRunner(tmp_db, migrations_dir)
        results = runner.run()
        assert len(results) == 1
        runner.close()

    def test_migration_tracking_table_schema(self, tmp_db, migrations_dir):
        _write_migration(
            migrations_dir,
            "001_init.sql",
            "CREATE TABLE t1 (id INTEGER PRIMARY KEY);",
        )

        runner = MigrationRunner(tmp_db, migrations_dir)
        runner.run()

        # Verify _migrations table has correct columns
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute("PRAGMA table_info(_migrations)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        assert "version" in columns
        assert "name" in columns
        assert "applied_at" in columns
        assert "checksum" in columns
        conn.close()
        runner.close()
