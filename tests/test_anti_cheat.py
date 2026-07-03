"""Tests for anti-cheat engine module."""

import numpy as np
import pytest

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.anti_cheat import AntiCheatEngine


class TestCheckCopresence:
    """Tests for the co-presence rule (Rule A)."""

    def test_both_detected_returns_ok(self):
        engine = AntiCheatEngine()
        assert engine.check_copresence(badge_detected=True, body_detected=True) == 'OK'

    def test_badge_no_body_returns_badge_no_body(self):
        engine = AntiCheatEngine()
        assert engine.check_copresence(badge_detected=True, body_detected=False) == 'BADGE_NO_BODY'

    def test_body_no_badge_returns_body_no_badge(self):
        engine = AntiCheatEngine()
        assert engine.check_copresence(badge_detected=False, body_detected=True) == 'BODY_NO_BADGE'

    def test_neither_detected_returns_none(self):
        engine = AntiCheatEngine()
        assert engine.check_copresence(badge_detected=False, body_detected=False) == 'NONE'


class TestCheckMovement:
    """Tests for the micro-movement rule (Rule B)."""

    def test_none_crop_returns_no_data(self):
        engine = AntiCheatEngine(clock=lambda: 0.0)
        assert engine.check_movement(None) == 'NO_DATA'

    def test_first_crop_returns_moving(self):
        engine = AntiCheatEngine(clock=lambda: 0.0)
        crop = np.zeros((50, 50, 3), dtype=np.uint8)
        assert engine.check_movement(crop) == 'MOVING'

    def test_identical_crops_returns_static(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0])
        crop = np.ones((50, 50, 3), dtype=np.uint8) * 128
        engine.check_movement(crop)  # First frame — MOVING
        t[0] = 1.0
        assert engine.check_movement(crop.copy()) == 'STATIC'

    def test_different_crops_returns_moving(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0], use_optical_flow=False)
        crop1 = np.zeros((50, 50, 3), dtype=np.uint8)
        crop2 = np.ones((50, 50, 3), dtype=np.uint8) * 100  # Large difference
        engine.check_movement(crop1)
        t[0] = 1.0
        assert engine.check_movement(crop2) == 'MOVING'

    def test_static_for_180_seconds_returns_abandoned(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0])
        crop = np.ones((50, 50, 3), dtype=np.uint8) * 128

        # First frame
        engine.check_movement(crop)

        # Second frame — starts static timer
        t[0] = 1.0
        result = engine.check_movement(crop.copy())
        assert result == 'STATIC'

        # After 180 seconds — should trigger ABANDONED
        t[0] = 181.0
        result = engine.check_movement(crop.copy())
        assert result == 'ABANDONED'

    def test_movement_resets_static_timer(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0], use_optical_flow=False)
        crop_static = np.ones((50, 50, 3), dtype=np.uint8) * 128
        crop_moved = np.ones((50, 50, 3), dtype=np.uint8) * 200

        # First frame
        engine.check_movement(crop_static)

        # Static for 170 seconds
        t[0] = 170.0
        engine.check_movement(crop_static.copy())

        # Movement detected at 170s — resets timer
        t[0] = 171.0
        result = engine.check_movement(crop_moved)
        assert result == 'MOVING'

        # After another 100s static (total 271s from start, but only 100s since movement)
        t[0] = 272.0
        result = engine.check_movement(crop_moved.copy())
        assert result == 'STATIC'  # not ABANDONED yet because timer was reset

    def test_none_after_crop_resets_state(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0])
        crop = np.ones((50, 50, 3), dtype=np.uint8) * 128

        engine.check_movement(crop)
        t[0] = 100.0
        engine.check_movement(crop.copy())  # STATIC
        engine.check_movement(None)  # resets

        t[0] = 200.0
        # Fresh start after reset
        assert engine.check_movement(crop) == 'MOVING'

    def test_shape_change_resets_comparison(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0], use_optical_flow=False)
        crop_small = np.ones((50, 50, 3), dtype=np.uint8) * 128
        crop_big = np.ones((100, 100, 3), dtype=np.uint8) * 128

        engine.check_movement(crop_small)
        t[0] = 1.0
        # Different shape — treated as new start
        assert engine.check_movement(crop_big) == 'MOVING'


class TestReset:
    """Tests for the reset method."""

    def test_reset_clears_state(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0])
        crop = np.ones((50, 50, 3), dtype=np.uint8) * 128

        engine.check_movement(crop)
        t[0] = 100.0
        engine.check_movement(crop.copy())

        engine.reset()
        assert engine._prev_crop is None
        assert engine._still_since is None


class TestIsBadgeStatic:
    """Tests for the is_badge_static convenience method."""

    def test_returns_false_when_moving(self):
        engine = AntiCheatEngine(clock=lambda: 0.0)
        crop = np.zeros((50, 50, 3), dtype=np.uint8)
        assert engine.is_badge_static(crop) is False

    def test_returns_true_when_abandoned(self):
        t = [0.0]
        engine = AntiCheatEngine(clock=lambda: t[0])
        crop = np.ones((50, 50, 3), dtype=np.uint8) * 128

        engine.check_movement(crop)
        t[0] = 1.0
        engine.check_movement(crop.copy())  # start static timer

        t[0] = 182.0
        assert engine.is_badge_static(crop.copy()) is True

    def test_returns_false_when_no_data(self):
        engine = AntiCheatEngine(clock=lambda: 0.0)
        assert engine.is_badge_static(None) is False
