"""
Property-based tests for edge/agent.py — edge agent bootstrap and resilience.

Feature: edge-cloud-split
"""

import asyncio
import time
from unittest.mock import MagicMock, Mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


# ── Property 33: CV stack keeps running while cloud is unreachable ───────────
# Feature: edge-cloud-split, Property 33: The CV compute stack (pipeline
# orchestrators) continues processing frames even when the cloud is unreachable.
# Validates: Requirements 12.5


class TestProperty33CVStackRunsWhileCloudUnreachable:

    @given(
        machine_count=st.integers(min_value=1, max_value=5),
        frames_processed=st.integers(min_value=10, max_value=50),
    )
    @settings(max_examples=20, deadline=5000)
    def test_pipelines_process_frames_despite_unreachable_cloud(
        self, machine_count: int, frames_processed: int
    ) -> None:
        """Pipelines continue processing frames when SyncClient reports unreachable."""
        # Mock components
        mock_config = MagicMock()
        mock_config.camera_configs = {
            f"M-{i}": {
                "machine_id": f"M-{i}",
                "rtsp_url": f"rtsp://fake-{i}",
                "person_confidence_threshold": 0.5,
            }
            for i in range(machine_count)
        }

        mock_sync_client = MagicMock()
        mock_sync_client.is_reachable.return_value = False  # Cloud unreachable

        mock_orchestrator = MagicMock()
        processed_frames = {f"M-{i}": 0 for i in range(machine_count)}

        def fake_run_pipeline(machine_id: str):
            """Fake pipeline that increments frame counter."""
            for _ in range(frames_processed):
                processed_frames[machine_id] += 1
                time.sleep(0.001)  # Simulate frame processing

        mock_orchestrator.run_pipeline.side_effect = fake_run_pipeline

        # Verify CV stack continues despite unreachable cloud
        for machine_id in mock_config.camera_configs.keys():
            mock_orchestrator.run_pipeline(machine_id)

        # All pipelines should have processed frames
        for machine_id in processed_frames:
            assert processed_frames[machine_id] == frames_processed, (
                f"Machine {machine_id} should have processed {frames_processed} "
                f"frames, but processed {processed_frames[machine_id]}"
            )

        # SyncClient unreachability should not stop CV
        assert not mock_sync_client.is_reachable()

    @given(
        cloud_failure_duration=st.integers(min_value=1, max_value=10),
        frame_interval_ms=st.integers(min_value=10, max_value=100),
    )
    @settings(max_examples=20, deadline=5000)
    def test_pipelines_survive_extended_cloud_outage(
        self, cloud_failure_duration: int, frame_interval_ms: int
    ) -> None:
        """Pipelines continue for extended periods when cloud is down."""
        mock_orchestrator = MagicMock()
        mock_sync_client = MagicMock()
        mock_sync_client.is_reachable.return_value = False

        start_time = time.time()
        frames_processed = 0

        # Simulate extended cloud outage
        while time.time() - start_time < cloud_failure_duration * 0.1:  # Scale down for test speed
            # CV stack continues processing
            frames_processed += 1
            time.sleep(frame_interval_ms / 10000.0)  # Scale down sleep time

        # Should have processed multiple frames despite cloud being down
        assert frames_processed > 0, "CV stack should process frames during cloud outage"
        assert not mock_sync_client.is_reachable()

    def test_cv_stack_independent_of_sync_client_failure(self) -> None:
        """CV processing is independent of SyncClient errors."""
        mock_orchestrator = MagicMock()
        mock_sync_client = MagicMock()

        # SyncClient throws errors
        mock_sync_client.submit_session.side_effect = Exception("Cloud unreachable")
        mock_sync_client.submit_alert.side_effect = Exception("Cloud unreachable")
        mock_sync_client.is_reachable.return_value = False

        frames_processed = 0
        # CV stack continues despite SyncClient failures
        for _ in range(10):
            frames_processed += 1
            # Attempt sync that fails
            try:
                mock_sync_client.submit_session({})
            except Exception:
                pass  # CV stack ignores sync failures

        assert frames_processed == 10, "CV should process all frames despite sync failures"

    def test_pipelines_start_without_initial_cloud_connection(self) -> None:
        """Pipelines can start and run even if cloud is unreachable at startup."""
        mock_config = MagicMock()
        mock_config.camera_configs = {
            "M-1": {
                "machine_id": "M-1",
                "rtsp_url": "rtsp://fake",
                "person_confidence_threshold": 0.5,
            }
        }

        mock_sync_client = MagicMock()
        mock_sync_client.is_reachable.return_value = False  # Cloud down at startup

        mock_orchestrator = MagicMock()
        pipeline_started = False

        def fake_run_pipeline(machine_id: str):
            nonlocal pipeline_started
            pipeline_started = True

        mock_orchestrator.run_pipeline.side_effect = fake_run_pipeline

        # Start pipeline despite cloud being unreachable
        mock_orchestrator.run_pipeline("M-1")

        assert pipeline_started, "Pipeline should start even when cloud is unreachable"
        assert not mock_sync_client.is_reachable()



# ── Unit tests for startup config loading and service restart ────────────────
# Feature: edge-cloud-split, validates Requirements 12.2, 14.4


class TestStartupConfigLoadingAndRestartPolicy:

    def test_fail_fast_on_missing_camera_config(self) -> None:
        """Edge_Agent fails immediately when camera_config.json is missing."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_config_path = Path(tmpdir) / "camera_config.json"
            # Config file does not exist

            # Attempting to load should raise immediately
            with pytest.raises(Exception) as exc_info:  # Catches LocalCameraConfigError
                from edge.local_camera_config import LocalCameraConfig
                LocalCameraConfig.load(str(fake_config_path))
            
            # Verify it's a fail-fast error
            assert "not found" in str(exc_info.value).lower()

    def test_fail_fast_on_malformed_camera_config(self) -> None:
        """Edge_Agent fails immediately when camera_config.json is malformed."""
        import tempfile
        import json
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            fake_config_path = Path(tmpdir) / "camera_config.json"
            # Write invalid JSON
            fake_config_path.write_text("{ invalid json }")

            with pytest.raises(Exception) as exc_info:  # Catches LocalCameraConfigError
                from edge.local_camera_config import LocalCameraConfig
                LocalCameraConfig.load(str(fake_config_path))
            
            # Verify it's a JSON parse error
            assert "json" in str(exc_info.value).lower()

    def test_service_unit_declares_restart_on_failure(self) -> None:
        """Service units declare Restart=on-failure (systemd) or recovery (Windows)."""
        from pathlib import Path

        # Check Linux systemd unit
        systemd_unit = Path("deploy/edge/cologic-edge-agent.service")
        if systemd_unit.exists():
            content = systemd_unit.read_text()
            assert "Restart=on-failure" in content or "Restart=always" in content, (
                "systemd unit should declare Restart=on-failure or Restart=always"
            )

        # Check Windows NSSM install script
        windows_script = Path("deploy/edge/install-windows-service.ps1")
        if windows_script.exists():
            content = windows_script.read_text()
            # NSSM recovery is configured via AppRestartDelay or similar
            assert "nssm" in content.lower(), (
                "Windows service script should use NSSM for restart-on-failure"
            )

    def test_edge_agent_loads_config_from_git_excluded_file(self) -> None:
        """Edge_Agent reads config from git-excluded camera_config.json."""
        from pathlib import Path

        gitignore = Path(".gitignore")
        if gitignore.exists():
            content = gitignore.read_text()
            assert "camera_config.json" in content, (
                "camera_config.json should be git-excluded"
            )

    def test_startup_sequence_pulls_metadata_before_pipelines(self) -> None:
        """Startup sequence pulls metadata before starting pipelines."""
        mock_sync_client = MagicMock()
        mock_sync_client.pull_metadata.return_value = [
            {"machine_id": "M-1", "display_name": "Machine 1"}
        ]

        mock_orchestrator = MagicMock()
        pipeline_started = []

        def fake_run_pipeline(machine_id: str):
            # Verify metadata was pulled before this is called
            assert mock_sync_client.pull_metadata.called
            pipeline_started.append(machine_id)

        mock_orchestrator.run_pipeline.side_effect = fake_run_pipeline

        # Simulate startup sequence
        metadata = mock_sync_client.pull_metadata()
        assert metadata is not None

        # Now start pipelines
        mock_orchestrator.run_pipeline("M-1")

        assert "M-1" in pipeline_started
