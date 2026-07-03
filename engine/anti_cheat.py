"""Anti-cheat engine implementing co-presence and micro-movement rules.

Supports two movement detection backends:
- Optical flow (Farneback) — default, more robust to noise/lighting
- RMS pixel difference — legacy fallback
"""

import logging
import time as time_module
from typing import Optional, Callable

import cv2
import numpy as np

from config import (
    MOVEMENT_THRESHOLD,
    STATIC_BADGE_TIMEOUT_SECONDS,
    OPTICAL_FLOW_THRESHOLD,
    USE_OPTICAL_FLOW,
)

logger = logging.getLogger(__name__)


class AntiCheatEngine:
    """Enforces anti-cheat rules for session tracking.

    Rule A (Co-presence): Both badge AND body must be detected for active
    time to accumulate. Returns status indicating co-presence state.

    Rule B (Micro-movement): The body region must show actual human movement.
    Uses optical flow (Farneback) by default for robustness against camera
    noise and lighting changes. Falls back to RMS pixel diff if disabled.
    A person that remains static for > STATIC_BADGE_TIMEOUT_SECONDS (180s)
    triggers ABANDONED state.
    """

    def __init__(self, clock: Callable[[], float] = None, use_optical_flow: bool = None):
        """Initialize the anti-cheat engine.

        Args:
            clock: Optional time function returning seconds (for testing).
            use_optical_flow: Whether to use optical flow (default from config).
        """
        self._clock = clock or time_module.time
        self._use_optical_flow = use_optical_flow if use_optical_flow is not None else USE_OPTICAL_FLOW
        self._prev_crop: Optional[np.ndarray] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._still_since: Optional[float] = None

    def check_copresence(self, badge_detected: bool, body_detected: bool) -> str:
        """Check co-presence rule.

        Args:
            badge_detected: Whether a valid badge ID was detected.
            body_detected: Whether a person body was detected.

        Returns:
            'OK' — both detected, clock can tick
            'BADGE_NO_BODY' — badge visible but no body (EXCEPTION trigger)
            'BODY_NO_BADGE' — body visible but no badge (pause time)
            'NONE' — neither detected
        """
        if badge_detected and body_detected:
            return 'OK'
        if badge_detected and not body_detected:
            return 'BADGE_NO_BODY'
        if not badge_detected and body_detected:
            return 'BODY_NO_BADGE'
        return 'NONE'

    def check_movement(self, body_crop: Optional[np.ndarray]) -> str:
        """Check movement of the body region using optical flow or RMS diff.

        Args:
            body_crop: The body region as a numpy array, or None if no body.

        Returns:
            'MOVING' — body region shows sufficient movement
            'STATIC' — body is static but timeout not yet reached
            'ABANDONED' — body has been static for >= timeout seconds
            'NO_DATA' — no crop available for comparison
        """
        if body_crop is None:
            self._prev_crop = None
            self._prev_gray = None
            self._still_since = None
            return 'NO_DATA'

        if self._use_optical_flow:
            return self._check_movement_optical_flow(body_crop)
        else:
            return self._check_movement_rms(body_crop)

    def _check_movement_optical_flow(self, body_crop: np.ndarray) -> str:
        """Optical flow based movement detection (Farneback).

        Computes dense optical flow between consecutive frames in the body
        region and uses the mean flow magnitude as the movement score.
        More robust to camera noise and lighting changes than RMS diff.
        """
        # Convert to grayscale
        if len(body_crop.shape) == 3:
            gray = cv2.cvtColor(body_crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = body_crop

        # Resize to fixed size for consistent flow computation
        gray = cv2.resize(gray, (64, 128))

        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            self._still_since = None
            return 'MOVING'

        # Compute Farneback optical flow
        flow = cv2.calcOpticalFlowFarneback(
            self._prev_gray, gray,
            None,
            pyr_scale=0.5,
            levels=3,
            winsize=15,
            iterations=3,
            poly_n=5,
            poly_sigma=1.2,
            flags=0
        )

        # Calculate mean flow magnitude
        magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        mean_flow = float(np.mean(magnitude))

        self._prev_gray = gray
        now = self._clock()

        if mean_flow >= OPTICAL_FLOW_THRESHOLD:
            self._still_since = None
            return 'MOVING'
        else:
            if self._still_since is None:
                self._still_since = now

            elapsed = now - self._still_since
            if elapsed >= STATIC_BADGE_TIMEOUT_SECONDS:
                return 'ABANDONED'
            return 'STATIC'

    def _check_movement_rms(self, body_crop: np.ndarray) -> str:
        """Legacy RMS pixel difference movement detection."""
        crop_float = body_crop.astype(np.float32)

        if self._prev_crop is None or self._prev_crop.shape != crop_float.shape:
            self._prev_crop = crop_float
            self._still_since = None
            return 'MOVING'

        diff = crop_float - self._prev_crop
        rms = float(np.sqrt(np.mean(diff ** 2)))
        self._prev_crop = crop_float

        now = self._clock()

        if rms >= MOVEMENT_THRESHOLD:
            self._still_since = None
            return 'MOVING'
        else:
            if self._still_since is None:
                self._still_since = now

            elapsed = now - self._still_since
            if elapsed >= STATIC_BADGE_TIMEOUT_SECONDS:
                return 'ABANDONED'
            return 'STATIC'

    def reset(self):
        """Reset the engine state (call when session closes)."""
        self._prev_crop = None
        self._prev_gray = None
        self._still_since = None

    def is_badge_static(self, body_crop: Optional[np.ndarray]) -> bool:
        """Convenience method: returns True if check_movement returns ABANDONED."""
        result = self.check_movement(body_crop)
        return result == 'ABANDONED'
