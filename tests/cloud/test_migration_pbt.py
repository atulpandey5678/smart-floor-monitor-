"""Property-based tests for monolith→split migration.

Feature: edge-cloud-split

Covers one correctness property:
- Property 34: Migration splits credentials from cloud metadata
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from edge.camera_crypto import encrypt_rtsp_url
from scripts.migrate_monolith_to_split import run_migration


# ── Property 34: Migration splits credentials from cloud metadata ─────────────
# Feature: edge-cloud-split, Property 34: The migration correctly exports
# camera credentials (rtsp_url_encrypted) to Local_Camera_Config and keeps
# only credential-free Machine_Metadata in the cloud registry.
# Validates: Requirements 15.2 (credential split)


class TestProperty34MigrationSplitsCredentials:
    """Property 34 validation: migration splits credentials from cloud metadata."""

    @staticmethod
    def _create_test_db(db_path: str, machines: Dict[str, str]) -> None:
        """Create a minimal tracker.db with migrations 001-004 applied."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")

        # Minimal machines table schema (enough for the migration to work)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS machines (
                machine_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                detection_zone TEXT,
                person_confidence_threshold REAL,
                light_zone TEXT,
                rtsp_url_encrypted TEXT
            )
            """
        )

        # Sessions, alerts, machine_state_events tables for backfill testing
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT,
                badge_id TEXT,
                session_start TEXT NOT NULL,
                session_end TEXT,
                active_duration_seconds INTEGER,
                close_reason TEXT,
                event_id TEXT,
                session_uuid TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT,
                alert_type TEXT NOT NULL,
                message TEXT,
                timestamp TEXT NOT NULL,
                badge_id TEXT,
                event_id TEXT,
                event_image_url TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS machine_state_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id TEXT,
                previous_state TEXT,
                new_state TEXT,
                timestamp TEXT NOT NULL,
                event_id TEXT
            )
            """
        )

        # Insert machines with encrypted RTSP URLs
        for machine_id, rtsp_url in machines.items():
            encrypted = encrypt_rtsp_url(rtsp_url)
            conn.execute(
                """
                INSERT OR REPLACE INTO machines (
                    machine_id, display_name, rtsp_url_encrypted
                ) VALUES (?, ?, ?)
                """,
                (machine_id, f"Machine {machine_id}", encrypted),
            )

        conn.commit()
        conn.close()

    @staticmethod
    def _read_local_camera_config(camera_config_path: str) -> Dict[str, Any]:
        """Read and parse the Local_Camera_Config JSON file."""
        if not os.path.isfile(camera_config_path):
            return {}
        with open(camera_config_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _read_cloud_machines(db_path: str) -> Dict[str, Dict[str, Any]]:
        """Read machines table from the cloud DB."""
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM machines").fetchall()
        conn.close()
        result = {}
        for row in rows:
            result[row["machine_id"]] = dict(row)
        return result

    @given(
        machine_count=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100, deadline=None)
    def test_migration_exports_credentials_to_edge_config(
        self, machine_count: int
    ) -> None:
        """Migration exports rtsp_url_encrypted to Local_Camera_Config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.db")
            camera_config_path = os.path.join(tmpdir, "camera_config.json")

            # Generate test machines with unique RTSP URLs
            machines = {
                f"M-{i:02d}": f"rtsp://admin:pass{i}@192.168.1.{10+i}:554/stream"
                for i in range(1, machine_count + 1)
            }
            self._create_test_db(db_path, machines)

            # Run migration (encrypted form by default)
            summary = run_migration(
                db_path,
                camera_config_path,
                default_machine_id="M-01",
                emit_plaintext=False,
                scrub=False,
                apply_schema=False,
            )

            # Verify Local_Camera_Config contains all machines
            edge_config = self._read_local_camera_config(camera_config_path)
            assert len(edge_config) == machine_count, (
                f"Expected {machine_count} machines in edge config, "
                f"got {len(edge_config)}"
            )
            for machine_id in machines:
                assert machine_id in edge_config, (
                    f"Machine {machine_id} not found in edge config"
                )
                # Encrypted form should have rtsp_url_encrypted key
                assert "rtsp_url_encrypted" in edge_config[machine_id], (
                    f"Machine {machine_id} missing rtsp_url_encrypted"
                )

            # Verify summary reports all exported
            assert len(summary.exported_machine_ids) == machine_count
            assert len(summary.skipped_machine_ids) == 0

    @given(
        machine_count=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=100, deadline=None)
    def test_migration_with_plaintext_option(self, machine_count: int) -> None:
        """Migration with --emit-plaintext writes rtsp_url instead of ciphertext."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.db")
            camera_config_path = os.path.join(tmpdir, "camera_config.json")

            machines = {
                f"M-{i:02d}": f"rtsp://admin:pass{i}@192.168.1.{10+i}:554/stream"
                for i in range(1, machine_count + 1)
            }
            self._create_test_db(db_path, machines)

            # Run migration with plaintext
            run_migration(
                db_path,
                camera_config_path,
                default_machine_id="M-01",
                emit_plaintext=True,
                scrub=False,
                apply_schema=False,
            )

            edge_config = self._read_local_camera_config(camera_config_path)
            for machine_id, rtsp_url in machines.items():
                assert "rtsp_url" in edge_config[machine_id], (
                    f"Machine {machine_id} missing rtsp_url (plaintext)"
                )
                # Verify the plaintext URL matches
                assert edge_config[machine_id]["rtsp_url"] == rtsp_url, (
                    f"Machine {machine_id} rtsp_url mismatch"
                )

    @given(
        machine_count=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_migration_with_scrub_removes_cloud_credentials(
        self, machine_count: int
    ) -> None:
        """Migration with --scrub-cloud-credentials blanks rtsp_url_encrypted in DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.db")
            camera_config_path = os.path.join(tmpdir, "camera_config.json")

            machines = {
                f"M-{i:02d}": f"rtsp://admin:pass{i}@192.168.1.{10+i}:554/stream"
                for i in range(1, machine_count + 1)
            }
            self._create_test_db(db_path, machines)

            # Run migration with scrub
            summary = run_migration(
                db_path,
                camera_config_path,
                default_machine_id="M-01",
                emit_plaintext=False,
                scrub=True,
                apply_schema=False,
            )

            # Verify cloud DB has blanked rtsp_url_encrypted
            cloud_machines = self._read_cloud_machines(db_path)
            for machine_id in machines:
                rtsp_encrypted = cloud_machines[machine_id]["rtsp_url_encrypted"]
                assert rtsp_encrypted == "" or rtsp_encrypted is None, (
                    f"Machine {machine_id} still has credentials in cloud DB: "
                    f"{rtsp_encrypted!r}"
                )

            # Verify scrubbed count matches
            assert summary.scrubbed_cloud_credentials == machine_count

    @given(
        machine_count=st.integers(min_value=1, max_value=3),
    )
    @settings(max_examples=50, deadline=None)
    def test_migration_preserves_metadata_columns(self, machine_count: int) -> None:
        """Migration preserves non-credential machine metadata in cloud DB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.db")
            camera_config_path = os.path.join(tmpdir, "camera_config.json")

            machines = {
                f"M-{i:02d}": f"rtsp://admin:pass{i}@192.168.1.{10+i}:554/stream"
                for i in range(1, machine_count + 1)
            }
            self._create_test_db(db_path, machines)

            # Run migration with scrub
            run_migration(
                db_path,
                camera_config_path,
                default_machine_id="M-01",
                emit_plaintext=False,
                scrub=True,
                apply_schema=False,
            )

            # Verify metadata columns are intact
            cloud_machines = self._read_cloud_machines(db_path)
            for machine_id in machines:
                machine = cloud_machines[machine_id]
                assert machine["machine_id"] == machine_id
                assert machine["display_name"] == f"Machine {machine_id}"
                # Metadata columns preserved
                assert "detection_zone" in machine
                assert "person_confidence_threshold" in machine
                assert "light_zone" in machine

    def test_migration_is_idempotent(self) -> None:
        """Running migration twice produces the same result without errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "tracker.db")
            camera_config_path = os.path.join(tmpdir, "camera_config.json")

            machines = {
                "M-01": "rtsp://admin:pass1@192.168.1.10:554/stream",
                "M-02": "rtsp://admin:pass2@192.168.1.11:554/stream",
            }
            self._create_test_db(db_path, machines)

            # First run
            summary1 = run_migration(
                db_path,
                camera_config_path,
                default_machine_id="M-01",
                emit_plaintext=False,
                scrub=False,
                apply_schema=False,
            )

            # Second run
            summary2 = run_migration(
                db_path,
                camera_config_path,
                default_machine_id="M-01",
                emit_plaintext=False,
                scrub=False,
                apply_schema=False,
            )

            # Both runs export the same machines
            assert set(summary1.exported_machine_ids) == set(summary2.exported_machine_ids)

            # Edge config still has both machines
            edge_config = self._read_local_camera_config(camera_config_path)
            assert len(edge_config) == 2
            assert "M-01" in edge_config
            assert "M-02" in edge_config
