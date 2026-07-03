# Tests for cv_pipeline/capture.py - frame skipping logic

import sys
import os
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from cv_pipeline.capture import FrameCapture


class TestReadFrameWithSkip:
    """Tests for read_frame_with_skip method."""

    def _make_capture_with_mock(self):
        """Create a FrameCapture instance with a mocked VideoCapture."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        capture._cap = mock_cap
        return capture, mock_cap

    def test_returns_false_when_capture_not_open(self):
        """Should return (False, None) if capture is not opened."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        success, frame = capture.read_frame_with_skip()
        assert success is False
        assert frame is None

    def test_returns_false_when_cap_is_none(self):
        """Should return (False, None) if _cap is None."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        capture._cap = None
        success, frame = capture.read_frame_with_skip()
        assert success is False
        assert frame is None

    @patch("cv_pipeline.capture.FRAME_SKIP", 3)
    def test_grabs_n_minus_1_times_then_retrieves(self):
        """With FRAME_SKIP=3, should grab 2 times then retrieve 1."""
        capture, mock_cap = self._make_capture_with_mock()
        fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_cap.grab.return_value = True
        mock_cap.retrieve.return_value = (True, fake_frame)

        success, frame = capture.read_frame_with_skip()

        assert success is True
        assert frame is not None
        assert mock_cap.grab.call_count == 2
        assert mock_cap.retrieve.call_count == 1

    @patch("cv_pipeline.capture.FRAME_SKIP", 1)
    def test_frame_skip_1_no_grabs(self):
        """With FRAME_SKIP=1, should grab 0 times and just retrieve."""
        capture, mock_cap = self._make_capture_with_mock()
        fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_cap.grab.return_value = True
        mock_cap.retrieve.return_value = (True, fake_frame)

        success, frame = capture.read_frame_with_skip()

        assert success is True
        assert mock_cap.grab.call_count == 0
        assert mock_cap.retrieve.call_count == 1

    @patch("cv_pipeline.capture.FRAME_SKIP", 5)
    def test_frame_skip_5_grabs_4_times(self):
        """With FRAME_SKIP=5, should grab 4 times then retrieve."""
        capture, mock_cap = self._make_capture_with_mock()
        fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_cap.grab.return_value = True
        mock_cap.retrieve.return_value = (True, fake_frame)

        success, frame = capture.read_frame_with_skip()

        assert success is True
        assert mock_cap.grab.call_count == 4
        assert mock_cap.retrieve.call_count == 1

    @patch("cv_pipeline.capture.FRAME_SKIP", 3)
    def test_returns_false_when_grab_fails(self):
        """Should return (False, None) if any grab() call fails."""
        capture, mock_cap = self._make_capture_with_mock()
        mock_cap.grab.side_effect = [True, False]  # Second grab fails

        success, frame = capture.read_frame_with_skip()

        assert success is False
        assert frame is None

    @patch("cv_pipeline.capture.FRAME_SKIP", 3)
    def test_increments_frame_count_on_success(self):
        """frame_count should increment after a successful read."""
        capture, mock_cap = self._make_capture_with_mock()
        fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_cap.grab.return_value = True
        mock_cap.retrieve.return_value = (True, fake_frame)

        assert capture.frame_count == 0

        capture.read_frame_with_skip()
        assert capture.frame_count == 1

        capture.read_frame_with_skip()
        assert capture.frame_count == 2

    @patch("cv_pipeline.capture.FRAME_SKIP", 3)
    def test_does_not_increment_frame_count_on_failure(self):
        """frame_count should not increment if grab fails."""
        capture, mock_cap = self._make_capture_with_mock()
        mock_cap.grab.return_value = False

        capture.read_frame_with_skip()
        assert capture.frame_count == 0


class TestFrameCountProperty:
    """Tests for the frame_count property."""

    def test_starts_at_zero(self):
        """frame_count should start at 0."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        assert capture.frame_count == 0

    @patch("cv_pipeline.capture.FRAME_SKIP", 1)
    def test_tracks_total_frames_processed(self):
        """frame_count should reflect total successful retrievals."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.grab.return_value = True
        fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        mock_cap.retrieve.return_value = (True, fake_frame)
        capture._cap = mock_cap

        for _ in range(5):
            capture.read_frame_with_skip()

        assert capture.frame_count == 5


class TestReconnect:
    """Tests for reconnection logic."""

    @patch("cv_pipeline.capture.time.sleep")
    def test_reconnect_releases_current_capture(self, mock_sleep):
        """reconnect() should release the current capture before retrying."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        capture._cap = mock_cap

        with patch.object(capture, "open", return_value=True):
            capture.reconnect()

        mock_cap.release.assert_called_once()

    @patch("cv_pipeline.capture.time.sleep")
    def test_reconnect_waits_5_seconds(self, mock_sleep):
        """reconnect() should wait 5 seconds before retrying."""
        capture = FrameCapture(rtsp_url="rtsp://test")

        with patch.object(capture, "open", return_value=True):
            capture.reconnect()

        mock_sleep.assert_called_once_with(5)

    @patch("cv_pipeline.capture.time.sleep")
    def test_reconnect_returns_true_on_success(self, mock_sleep):
        """reconnect() should return True when re-open succeeds."""
        capture = FrameCapture(rtsp_url="rtsp://test")

        with patch.object(capture, "open", return_value=True):
            result = capture.reconnect()

        assert result is True

    @patch("cv_pipeline.capture.time.sleep")
    def test_reconnect_returns_false_on_failure(self, mock_sleep):
        """reconnect() should return False when re-open fails."""
        capture = FrameCapture(rtsp_url="rtsp://test")

        with patch.object(capture, "open", return_value=False):
            result = capture.reconnect()

        assert result is False


class TestOpenWithRetry:
    """Tests for open_with_retry startup retry logic."""

    @patch("cv_pipeline.capture.time.sleep")
    def test_returns_immediately_on_first_success(self, mock_sleep):
        """open_with_retry() should return True immediately if open succeeds."""
        capture = FrameCapture(rtsp_url="rtsp://test")

        with patch.object(capture, "open", return_value=True):
            result = capture.open_with_retry()

        assert result is True
        mock_sleep.assert_not_called()

    @patch("cv_pipeline.capture.time.sleep")
    def test_retries_until_success(self, mock_sleep):
        """open_with_retry() should retry every 5 seconds until connected."""
        capture = FrameCapture(rtsp_url="rtsp://test")

        with patch.object(capture, "open", side_effect=[False, False, True]):
            result = capture.open_with_retry()

        assert result is True
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(5)


class TestReadFrameReconnection:
    """Tests for read_frame reconnection on failure."""

    @patch("cv_pipeline.capture.time.sleep")
    def test_read_frame_reconnects_when_not_open(self, mock_sleep):
        """read_frame() should attempt reconnection if capture not open."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        capture._cap = None

        with patch.object(capture, "reconnect", return_value=False) as mock_reconnect:
            success, frame = capture.read_frame()

        assert success is False
        assert frame is None
        mock_reconnect.assert_called_once()

    @patch("cv_pipeline.capture.time.sleep")
    def test_read_frame_reconnects_on_read_failure(self, mock_sleep):
        """read_frame() should reconnect if cap.read() returns failure."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.read.return_value = (False, None)
        capture._cap = mock_cap

        with patch.object(capture, "reconnect", return_value=False) as mock_reconnect:
            success, frame = capture.read_frame()

        assert success is False
        assert frame is None
        mock_reconnect.assert_called_once()

    @patch("cv_pipeline.capture.time.sleep")
    def test_read_frame_succeeds_after_reconnection(self, mock_sleep):
        """read_frame() should succeed if reconnection works and read works."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True

        fake_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        # First read fails, then after reconnect the second read succeeds
        mock_cap.read.side_effect = [(False, None), (True, fake_frame)]
        capture._cap = mock_cap

        def reconnect_side_effect():
            # After reconnect, cap is still the same mock
            return True

        with patch.object(capture, "reconnect", side_effect=reconnect_side_effect):
            success, frame = capture.read_frame()

        assert success is True
        assert frame is not None


class TestReadFrameWithSkipReconnection:
    """Tests for read_frame_with_skip reconnection on failure."""

    @patch("cv_pipeline.capture.time.sleep")
    def test_reconnects_when_capture_not_open(self, mock_sleep):
        """read_frame_with_skip() should reconnect if capture is not open."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        capture._cap = None

        with patch.object(capture, "reconnect", return_value=False) as mock_reconnect:
            success, frame = capture.read_frame_with_skip()

        assert success is False
        assert frame is None
        mock_reconnect.assert_called_once()

    @patch("cv_pipeline.capture.FRAME_SKIP", 3)
    @patch("cv_pipeline.capture.time.sleep")
    def test_reconnects_when_grab_fails(self, mock_sleep):
        """read_frame_with_skip() should reconnect if grab fails mid-skip."""
        capture = FrameCapture(rtsp_url="rtsp://test")
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cap.grab.return_value = False
        capture._cap = mock_cap

        with patch.object(capture, "reconnect", return_value=False) as mock_reconnect:
            success, frame = capture.read_frame_with_skip()

        assert success is False
        assert frame is None
        mock_reconnect.assert_called_once()
