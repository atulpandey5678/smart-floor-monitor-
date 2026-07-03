"""
Verification test for config.py defaults.

Validates: Requirements 15.2 (documented defaults for all parameters)

Checks:
1. All parameters have valid default values (not None unless explicitly allowed)
2. All parameters are of expected types and within valid ranges
3. The config module can be imported without error (app can start without modifications)
"""
import pytest
import sys
import os

# Ensure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


class TestConfigDefaults:
    """Verify all configuration defaults are valid and usable."""

    def test_config_imports_without_error(self):
        """Config module can be imported without raising any exceptions."""
        # If we got here, the import succeeded
        assert config is not None

    # ── Camera settings ──────────────────────────────────────

    def test_rtsp_url_is_string(self):
        assert isinstance(config.RTSP_URL, str)
        assert len(config.RTSP_URL) > 0

    def test_frame_skip_valid(self):
        assert isinstance(config.FRAME_SKIP, int)
        assert config.FRAME_SKIP >= 1

    def test_frame_width_valid(self):
        assert isinstance(config.FRAME_WIDTH, int)
        assert config.FRAME_WIDTH > 0

    def test_frame_height_valid(self):
        assert isinstance(config.FRAME_HEIGHT, int)
        assert config.FRAME_HEIGHT > 0

    # ── Detection zones ──────────────────────────────────────

    def test_detection_zone_valid(self):
        """Detection zone is a tuple of 4 floats in [0.0, 1.0]."""
        zone = config.DETECTION_ZONE
        assert isinstance(zone, tuple)
        assert len(zone) == 4
        x1, y1, x2, y2 = zone
        for val in (x1, y1, x2, y2):
            assert isinstance(val, float)
            assert 0.0 <= val <= 1.0
        # x2 > x1 and y2 > y1 (valid rectangle)
        assert x2 > x1
        assert y2 > y1

    def test_ocr_zone_valid(self):
        """OCR zone is a dict with x1, y1, x2, y2 keys, all floats in [0.0, 1.0]."""
        zone = config.OCR_ZONE
        assert isinstance(zone, dict)
        required_keys = {'x1', 'y1', 'x2', 'y2'}
        assert required_keys.issubset(zone.keys())
        for key in required_keys:
            assert isinstance(zone[key], float)
            assert 0.0 <= zone[key] <= 1.0
        assert zone['x2'] > zone['x1']
        assert zone['y2'] > zone['y1']

    # ── Detection thresholds ─────────────────────────────────

    def test_person_confidence_threshold_valid(self):
        assert isinstance(config.PERSON_CONFIDENCE_THRESHOLD, float)
        assert 0.0 < config.PERSON_CONFIDENCE_THRESHOLD <= 1.0

    def test_badge_confidence_threshold_valid(self):
        assert isinstance(config.BADGE_CONFIDENCE_THRESHOLD, float)
        assert 0.0 < config.BADGE_CONFIDENCE_THRESHOLD <= 1.0

    def test_badge_id_digit_range_valid(self):
        assert isinstance(config.BADGE_ID_MIN_DIGITS, int)
        assert isinstance(config.BADGE_ID_MAX_DIGITS, int)
        assert config.BADGE_ID_MIN_DIGITS >= 1
        assert config.BADGE_ID_MAX_DIGITS >= config.BADGE_ID_MIN_DIGITS

    # ── Session rules ────────────────────────────────────────

    def test_stable_frames_required_valid(self):
        assert isinstance(config.STABLE_FRAMES_REQUIRED, int)
        assert config.STABLE_FRAMES_REQUIRED >= 1

    def test_grace_period_seconds_valid(self):
        assert isinstance(config.GRACE_PERIOD_SECONDS, int)
        assert config.GRACE_PERIOD_SECONDS > 0

    # ── Anti-cheat settings ──────────────────────────────────

    def test_movement_threshold_valid(self):
        assert isinstance(config.MOVEMENT_THRESHOLD, float)
        assert config.MOVEMENT_THRESHOLD > 0.0

    def test_static_badge_timeout_valid(self):
        assert isinstance(config.STATIC_BADGE_TIMEOUT_SECONDS, int)
        assert config.STATIC_BADGE_TIMEOUT_SECONDS > 0

    # ── Server settings ──────────────────────────────────────

    def test_api_host_valid(self):
        assert isinstance(config.API_HOST, str)
        assert len(config.API_HOST) > 0
        # Should be localhost for security
        assert config.API_HOST == '127.0.0.1'

    def test_api_port_valid(self):
        assert isinstance(config.API_PORT, int)
        assert 1 <= config.API_PORT <= 65535

    def test_db_path_valid(self):
        assert isinstance(config.DB_PATH, str)
        assert len(config.DB_PATH) > 0

    # ── Machine identification ───────────────────────────────

    def test_machine_id_valid(self):
        assert isinstance(config.MACHINE_ID, str)
        assert len(config.MACHINE_ID) > 0

    # ── Completeness check ───────────────────────────────────

    def test_all_expected_parameters_exist(self):
        """All parameters defined in the design document exist in config."""
        expected_params = [
            'RTSP_URL',
            'FRAME_SKIP',
            'FRAME_WIDTH',
            'FRAME_HEIGHT',
            'DETECTION_ZONE',
            'OCR_ZONE',
            'PERSON_CONFIDENCE_THRESHOLD',
            'BADGE_CONFIDENCE_THRESHOLD',
            'BADGE_ID_MIN_DIGITS',
            'BADGE_ID_MAX_DIGITS',
            'STABLE_FRAMES_REQUIRED',
            'GRACE_PERIOD_SECONDS',
            'MOVEMENT_THRESHOLD',
            'STATIC_BADGE_TIMEOUT_SECONDS',
            'API_HOST',
            'API_PORT',
            'DB_PATH',
            'MACHINE_ID',
        ]
        for param in expected_params:
            assert hasattr(config, param), f"Missing config parameter: {param}"
            assert getattr(config, param) is not None, f"Config parameter {param} is None"
