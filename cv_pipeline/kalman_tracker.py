"""Kalman Filter for body bounding box tracking.

Smooths body detections across frames and predicts position during brief
occlusions to prevent false GRACE state transitions from detection flicker.
"""

from typing import Optional, Tuple

import numpy as np

from config import KALMAN_PREDICT_FRAMES, KALMAN_PROCESS_NOISE


class KalmanBoxTracker:
    """Kalman filter tracker for a single bounding box.

    State vector: [cx, cy, w, h, vx, vy, vw, vh]
    where (cx, cy) is the center, (w, h) is width/height,
    and (vx, vy, vw, vh) are their velocities.

    Predicts position for up to KALMAN_PREDICT_FRAMES frames when
    no measurement is available, then declares the track lost.
    """

    def __init__(self, max_predict_frames: int = None, process_noise: float = None):
        """Initialize the Kalman tracker.

        Args:
            max_predict_frames: Max frames to predict without measurement.
            process_noise: Process noise covariance diagonal value.
        """
        self._max_predict = max_predict_frames or KALMAN_PREDICT_FRAMES
        self._process_noise = process_noise or KALMAN_PROCESS_NOISE
        self._initialized = False
        self._miss_count = 0

        # State: [cx, cy, w, h, vx, vy, vw, vh]
        self._x = np.zeros(8)  # state estimate
        self._P = np.eye(8) * 10  # error covariance

        # Transition matrix (constant velocity model)
        self._F = np.eye(8)
        self._F[0, 4] = 1  # cx += vx
        self._F[1, 5] = 1  # cy += vy
        self._F[2, 6] = 1  # w += vw
        self._F[3, 7] = 1  # h += vh

        # Measurement matrix (we observe cx, cy, w, h)
        self._H = np.zeros((4, 8))
        self._H[0, 0] = 1
        self._H[1, 1] = 1
        self._H[2, 2] = 1
        self._H[3, 3] = 1

        # Process noise
        self._Q = np.eye(8) * self._process_noise
        # Higher noise on velocity terms
        self._Q[4, 4] = self._process_noise * 10
        self._Q[5, 5] = self._process_noise * 10
        self._Q[6, 6] = self._process_noise * 5
        self._Q[7, 7] = self._process_noise * 5

        # Measurement noise
        self._R = np.eye(4) * 1.0

    def _bbox_to_state(self, bbox: Tuple[int, int, int, int]) -> np.ndarray:
        """Convert (x1, y1, x2, y2) to (cx, cy, w, h)."""
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        w = x2 - x1
        h = y2 - y1
        return np.array([cx, cy, w, h])

    def _state_to_bbox(self, state: np.ndarray) -> Tuple[int, int, int, int]:
        """Convert (cx, cy, w, h) to (x1, y1, x2, y2)."""
        cx, cy, w, h = state[:4]
        w = max(w, 1)
        h = max(h, 1)
        x1 = int(cx - w / 2)
        y1 = int(cy - h / 2)
        x2 = int(cx + w / 2)
        y2 = int(cy + h / 2)
        return (x1, y1, x2, y2)

    def update(self, bbox: Optional[Tuple[int, int, int, int]]) -> Tuple[bool, Optional[Tuple[int, int, int, int]]]:
        """Process a frame's detection result.

        Args:
            bbox: Detected bounding box (x1, y1, x2, y2) or None if no detection.

        Returns:
            Tuple of (body_detected, smoothed_bbox):
            - body_detected: True if we have a valid (measured or predicted) position
            - smoothed_bbox: The smoothed/predicted bounding box, or None if track is lost
        """
        if bbox is not None:
            # We have a measurement
            z = self._bbox_to_state(bbox)

            if not self._initialized:
                # First detection — initialize state
                self._x[:4] = z
                self._x[4:] = 0  # zero velocity
                self._initialized = True
                self._miss_count = 0
                return (True, bbox)

            # Predict step
            self._predict()

            # Update step (Kalman correction)
            y = z - self._H @ self._x  # innovation
            S = self._H @ self._P @ self._H.T + self._R  # innovation covariance
            K = self._P @ self._H.T @ np.linalg.inv(S)  # Kalman gain
            self._x = self._x + K @ y
            self._P = (np.eye(8) - K @ self._H) @ self._P

            self._miss_count = 0
            smoothed_bbox = self._state_to_bbox(self._x)
            return (True, smoothed_bbox)

        else:
            # No detection this frame
            if not self._initialized:
                return (False, None)

            self._miss_count += 1

            if self._miss_count <= self._max_predict:
                # Predict forward (coast)
                self._predict()
                predicted_bbox = self._state_to_bbox(self._x)
                return (True, predicted_bbox)
            else:
                # Track lost — too many misses
                return (False, None)

    def _predict(self):
        """Kalman predict step."""
        self._x = self._F @ self._x
        self._P = self._F @ self._P @ self._F.T + self._Q

    def reset(self):
        """Reset the tracker state."""
        self._initialized = False
        self._miss_count = 0
        self._x = np.zeros(8)
        self._P = np.eye(8) * 10

    @property
    def is_tracking(self) -> bool:
        """Whether the tracker currently has a valid track."""
        return self._initialized and self._miss_count <= self._max_predict

    @property
    def miss_count(self) -> int:
        """Number of consecutive frames without a measurement."""
        return self._miss_count
