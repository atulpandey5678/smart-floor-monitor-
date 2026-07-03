# cv_pipeline/detector.py — YOLOv8-nano person detection
"""Person detection using YOLOv8-nano in CPU mode."""

import torch
from typing import Optional

import numpy as np
from ultralytics import YOLO

from config import (
    DETECTION_ZONE,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    PERSON_CONFIDENCE_THRESHOLD,
)

# Fix for PyTorch 2.6+ weights_only default change
torch.serialization.add_safe_globals([])  # noqa


class PersonDetector:
    """Detects persons in a frame using YOLOv8-nano on CPU."""

    # Minimum overlap ratio (intersection area / box area) to consider
    # a detection as being "in the zone".
    ZONE_OVERLAP_THRESHOLD = 0.40

    def __init__(self) -> None:
        """Load the YOLOv8-nano model in CPU mode."""
        # Fix PyTorch 2.6+ weights_only default
        import functools
        _orig_load = torch.load
        @functools.wraps(_orig_load)
        def _patched_load(*args, **kwargs):
            kwargs.setdefault('weights_only', False)
            return _orig_load(*args, **kwargs)
        torch.load = _patched_load
        try:
            self.model = YOLO('yolov8n.pt')
        finally:
            torch.load = _orig_load
        self.device = 'cpu'

        # Convert fractional detection zone to pixel coordinates
        zx1, zy1, zx2, zy2 = DETECTION_ZONE
        self.zone_x1 = int(zx1 * FRAME_WIDTH)
        self.zone_y1 = int(zy1 * FRAME_HEIGHT)
        self.zone_x2 = int(zx2 * FRAME_WIDTH)
        self.zone_y2 = int(zy2 * FRAME_HEIGHT)

    def is_in_zone(self, bbox: tuple) -> bool:
        """Check whether a bounding box overlaps sufficiently with the detection zone.

        A detection is considered "in the zone" when the intersection area
        between the bounding box and the detection zone exceeds 40% of the
        bounding box's own area.

        Args:
            bbox: (x1, y1, x2, y2) pixel coordinates of the detection box.

        Returns:
            True if the overlap ratio exceeds ZONE_OVERLAP_THRESHOLD.
        """
        bx1, by1, bx2, by2 = bbox

        # Compute the box area
        box_area = (bx2 - bx1) * (by2 - by1)
        if box_area <= 0:
            return False

        # Compute intersection rectangle
        inter_x1 = max(bx1, self.zone_x1)
        inter_y1 = max(by1, self.zone_y1)
        inter_x2 = min(bx2, self.zone_x2)
        inter_y2 = min(by2, self.zone_y2)

        # No intersection if the rectangle is degenerate
        if inter_x2 <= inter_x1 or inter_y2 <= inter_y1:
            return False

        inter_area = (inter_x2 - inter_x1) * (inter_y2 - inter_y1)
        overlap_ratio = inter_area / box_area

        return overlap_ratio > self.ZONE_OVERLAP_THRESHOLD

    def detect(self, frame: np.ndarray) -> tuple[bool, Optional[tuple]]:
        """Run person detection on the full frame, filtering to the detection zone."""
        # Compute zone coords from ACTUAL frame dimensions (not config defaults)
        h_frame, w_frame = frame.shape[:2]
        zx1, zy1, zx2, zy2 = DETECTION_ZONE
        zone_x1 = int(zx1 * w_frame)
        zone_y1 = int(zy1 * h_frame)
        zone_x2 = int(zx2 * w_frame)
        zone_y2 = int(zy2 * h_frame)

        results = self.model.predict(
            source=frame,
            imgsz=640,
            conf=PERSON_CONFIDENCE_THRESHOLD,
            classes=[0],  # person class only
            iou=0.45,
            half=False,
            device=self.device,
            verbose=False,
        )

        detections = results[0].boxes

        if detections is None or len(detections) == 0:
            return (False, None)

        all_boxes = detections.xyxy.cpu().numpy()
        confidences = detections.conf.cpu().numpy()

        best_conf = -1.0
        best_bbox = None

        for idx in range(len(all_boxes)):
            box = all_boxes[idx]
            x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            bbox_tuple = (x1, y1, x2, y2)

            # Check overlap with zone using actual frame dimensions
            box_area = (x2 - x1) * (y2 - y1)
            if box_area <= 0:
                continue
            ix1, iy1 = max(x1, zone_x1), max(y1, zone_y1)
            ix2, iy2 = min(x2, zone_x2), min(y2, zone_y2)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            overlap = ((ix2-ix1) * (iy2-iy1)) / box_area
            if overlap > self.ZONE_OVERLAP_THRESHOLD and confidences[idx] > best_conf:
                best_conf = confidences[idx]
                best_bbox = bbox_tuple

        if best_bbox is None:
            return (False, None)
        return (True, best_bbox)
