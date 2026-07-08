"""Machine tower light detection via HSV color analysis.

Analyzes a configurable region of interest (ROI) in the camera frame to
determine the color of a machine's tower/stack indicator light.
"""

from collections import deque

import cv2
import numpy as np
import structlog

from config import (
    LIGHT_ZONE,
    LIGHT_HUE_GREEN,
    LIGHT_HUE_AMBER,
    LIGHT_HUE_RED_LOW,
    LIGHT_HUE_RED_HIGH,
    LIGHT_SATURATION_MIN,
    LIGHT_BRIGHTNESS_MIN,
    LIGHT_DOMINANCE_THRESHOLD,
    LIGHT_STABLE_FRAMES,
    LIGHT_DETECTION_ENABLED,
    LIGHT_ANALYSIS_SIZE,
    LIGHT_MIN_COLOR_PIXELS,
    LIGHT_VIVID_SATURATION,
)

logger = structlog.get_logger(__name__)


class LightDetector:
    """Detects machine tower light color from a camera frame ROI.

    Uses HSV color space analysis with temporal filtering to produce
    stable state classifications: GREEN, AMBER, RED, OFF, or UNKNOWN.
    """

    def __init__(self, zone=None):
        self._enabled = LIGHT_DETECTION_ENABLED
        self._zone = zone or LIGHT_ZONE  # Use per-machine zone if provided, else config default

        # HSV ranges
        self._hue_green = LIGHT_HUE_GREEN
        self._hue_amber = LIGHT_HUE_AMBER
        self._hue_red_low = LIGHT_HUE_RED_LOW
        self._hue_red_high = LIGHT_HUE_RED_HIGH
        self._sat_min = LIGHT_SATURATION_MIN
        self._val_min = LIGHT_BRIGHTNESS_MIN
        self._dominance_threshold = LIGHT_DOMINANCE_THRESHOLD
        self._stable_frames = LIGHT_STABLE_FRAMES
        self._analysis_size = LIGHT_ANALYSIS_SIZE
        self._min_color_pixels = LIGHT_MIN_COLOR_PIXELS
        self._vivid_sat = LIGHT_VIVID_SATURATION

        # Temporal state
        self._confirmed_status = "UNKNOWN"
        self._previous_status = None
        self._candidate_buffer: deque = deque(maxlen=self._stable_frames)
        self._last_diag = {"green_pct": 0.0, "amber_pct": 0.0, "red_pct": 0.0, "qualifying_pct": 0.0}

    @property
    def diagnostics(self) -> dict:
        """Last per-frame color analysis percentages (for tuning/overlay)."""
        return self._last_diag

    @property
    def status(self) -> str:
        """Current confirmed machine light status."""
        return self._confirmed_status

    @property
    def previous_status(self):
        """Previous confirmed status before the last transition (or None)."""
        return self._previous_status

    def set_zone(self, zone):
        """Update the light detection zone at runtime.

        Args:
            zone: tuple (x1, y1, x2, y2) with fractional coordinates.
        """
        self._zone = zone

    def reset(self):
        """Reset detector state back to initial UNKNOWN."""
        self._previous_status = self._confirmed_status if self._confirmed_status != "UNKNOWN" else None
        self._confirmed_status = "UNKNOWN"
        self._candidate_buffer.clear()

    def detect(self, frame: np.ndarray) -> dict:
        """Analyze a frame and return the current light status.

        Args:
            frame: BGR image (numpy array) from the camera.

        Returns:
            dict with keys:
                status: "GREEN" | "AMBER" | "RED" | "OFF" | "UNKNOWN"
                transition: True if the status just changed, False otherwise
                previous: previous status string or None
        """
        if not self._enabled:
            return {"status": self._confirmed_status, "transition": False, "previous": self._previous_status}

        # Validate zone
        x1, y1, x2, y2 = self._zone
        if x2 <= x1 or y2 <= y1:
            logger.error("LIGHT_ZONE defines zero or negative area: %s", self._zone)
            return {"status": "UNKNOWN", "transition": False, "previous": self._previous_status}

        # Crop ROI from frame
        h, w = frame.shape[:2]
        px1 = int(x1 * w)
        py1 = int(y1 * h)
        px2 = int(x2 * w)
        py2 = int(y2 * h)

        # Clamp to frame bounds
        px1 = max(0, min(px1, w - 1))
        py1 = max(0, min(py1, h - 1))
        px2 = max(px1 + 1, min(px2, w))
        py2 = max(py1 + 1, min(py2, h))

        roi = frame[py1:py2, px1:px2]
        if roi.size == 0:
            logger.error("Light ROI is empty after cropping")
            return {"status": "UNKNOWN", "transition": False, "previous": self._previous_status}

        # ── Magnify the zone so far/small lights have enough pixels to read ──
        # A distant light is only a few pixels; upscaling + light blur recovers
        # its color and smooths JPEG compression noise.
        rh, rw = roi.shape[:2]
        longest = max(rh, rw)
        if longest < self._analysis_size:
            scale = self._analysis_size / float(longest)
            roi = cv2.resize(roi, (max(1, int(rw * scale)), max(1, int(rh * scale))),
                             interpolation=cv2.INTER_LINEAR)
        roi = cv2.GaussianBlur(roi, (3, 3), 0)

        # Convert to HSV
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        h_ch = hsv[:, :, 0]
        s_ch = hsv[:, :, 1]
        v_ch = hsv[:, :, 2]

        total_pixels = h_ch.size

        # ── Find VIVID colored pixels (a real lit indicator), ignore dull background ──
        # Vivid = saturated enough to be a true color AND bright enough to be "on".
        vivid_mask = (s_ch >= self._vivid_sat) & (v_ch >= self._val_min)
        vivid_count = int(np.count_nonzero(vivid_mask))

        h_vivid = h_ch[vivid_mask] if vivid_count > 0 else np.array([])

        green_count = int(np.count_nonzero(
            (h_vivid >= self._hue_green[0]) & (h_vivid <= self._hue_green[1])
        )) if vivid_count > 0 else 0
        amber_count = int(np.count_nonzero(
            (h_vivid >= self._hue_amber[0]) & (h_vivid <= self._hue_amber[1])
        )) if vivid_count > 0 else 0
        red_low_count = int(np.count_nonzero(
            (h_vivid >= self._hue_red_low[0]) & (h_vivid <= self._hue_red_low[1])
        )) if vivid_count > 0 else 0
        red_high_count = int(np.count_nonzero(
            (h_vivid >= self._hue_red_high[0]) & (h_vivid <= self._hue_red_high[1])
        )) if vivid_count > 0 else 0
        red_count = red_low_count + red_high_count

        counts = {"GREEN": green_count, "AMBER": amber_count, "RED": red_count}
        best_color = max(counts, key=counts.get)
        best_count = counts[best_color]

        # Diagnostic percentages (fraction of whole zone)
        self._last_diag = {
            "green_pct": round(green_count / total_pixels * 100, 1),
            "amber_pct": round(amber_count / total_pixels * 100, 1),
            "red_pct": round(red_count / total_pixels * 100, 1),
            "qualifying_pct": round(vivid_count / total_pixels * 100, 1),
        }

        # ── Decide: ANY clear colored cluster wins (absolute count, distance-independent) ──
        # Only a small absolute number of vivid pixels of one color is needed, so a
        # far/small light still registers. Background gray/white never qualifies.
        if best_count >= self._min_color_pixels:
            raw_status = best_color
        else:
            # No clear color cluster anywhere → light is OFF or no color visible
            raw_status = "OFF"

        # Temporal filtering
        self._candidate_buffer.append(raw_status)
        transition = False

        if len(self._candidate_buffer) >= self._stable_frames:
            # Check if all recent frames agree
            if all(s == raw_status for s in self._candidate_buffer):
                if raw_status != self._confirmed_status:
                    # State transition
                    self._previous_status = self._confirmed_status
                    self._confirmed_status = raw_status
                    transition = True

        return {
            "status": self._confirmed_status,
            "transition": transition,
            "previous": self._previous_status,
            "raw_status": raw_status,
            "diagnostics": self._last_diag,
        }
