# Tests for person detector module
"""Unit tests for PersonDetector.is_in_zone logic."""

from unittest.mock import patch, MagicMock
import pytest


class TestIsInZone:
    """Test the detection zone overlap logic without loading the YOLO model."""

    @pytest.fixture(autouse=True)
    def setup_detector(self):
        """Create a PersonDetector with mocked YOLO model to test zone logic."""
        with patch('cv_pipeline.detector.YOLO') as mock_yolo:
            mock_yolo.return_value = MagicMock()
            from cv_pipeline.detector import PersonDetector
            # Override zone directly to a known sub-frame for testing
            self.detector = PersonDetector()
            # Manually set a known zone for predictable overlap tests
            # Zone covers x: 64-1216, y: 36-684 (matches 0.05-0.95 of 1280x720)
            self.detector.zone_x1 = 64
            self.detector.zone_y1 = 36
            self.detector.zone_x2 = 1216
            self.detector.zone_y2 = 684

    def test_bbox_fully_inside_zone(self):
        """A box fully inside the zone should pass (100% overlap > 40%)."""
        bbox = (200, 100, 400, 500)
        assert self.detector.is_in_zone(bbox) is True

    def test_bbox_fully_outside_zone(self):
        """A box completely outside the zone should fail (0% overlap)."""
        bbox = (0, 0, 50, 50)
        assert self.detector.is_in_zone(bbox) is False

    def test_bbox_partially_overlapping_above_threshold(self):
        """A box with >40% overlap should pass."""
        # Box: x=0..200, y=100..400. Box area = 200*300 = 60000
        # Intersection: x=64..200, y=100..400 = 136*300 = 40800
        # Ratio = 40800/60000 = 0.68 > 0.40
        bbox = (0, 100, 200, 400)
        assert self.detector.is_in_zone(bbox) is True

    def test_bbox_partially_overlapping_below_threshold(self):
        """A box with <=40% overlap should fail."""
        # Box: x=0..80, y=100..400. Box area = 80*300 = 24000
        # Intersection: x=64..80, y=100..400 = 16*300 = 4800
        # Ratio = 4800/24000 = 0.20 < 0.40
        bbox = (0, 100, 80, 400)
        assert self.detector.is_in_zone(bbox) is False

    def test_bbox_zero_area(self):
        """A degenerate box with zero area should fail."""
        bbox = (100, 100, 100, 100)
        assert self.detector.is_in_zone(bbox) is False

    def test_bbox_negative_area(self):
        """A box with inverted coordinates should fail."""
        bbox = (400, 400, 200, 200)
        assert self.detector.is_in_zone(bbox) is False

    def test_bbox_at_zone_boundary_exactly(self):
        """A box touching the zone edge without overlapping should fail."""
        bbox = (0, 100, 64, 400)  # x2 == zone_x1, no intersection
        assert self.detector.is_in_zone(bbox) is False

    def test_bbox_on_right_edge_mostly_outside(self):
        """A box mostly to the right of the zone should fail."""
        # Box: x=1200..1280, y=100..400. Box area = 80*300 = 24000
        # Intersection: x=1200..1216, y=100..400 = 16*300 = 4800
        # Ratio = 4800/24000 = 0.20 < 0.40
        bbox = (1200, 100, 1280, 400)
        assert self.detector.is_in_zone(bbox) is False

    def test_bbox_spanning_entire_frame(self):
        """A box spanning the entire frame should pass."""
        bbox = (0, 0, 1280, 720)
        # Box area = 921600, Intersection = 1152*648 = 746496
        # Ratio = 0.81 > 0.40
        assert self.detector.is_in_zone(bbox) is True
