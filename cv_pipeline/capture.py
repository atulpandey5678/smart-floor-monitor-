# Frame capture module - RTSP stream capture with reconnection

import threading
import time

import cv2
import structlog

from config import RTSP_URL, FRAME_WIDTH, FRAME_HEIGHT, FRAME_SKIP

logger = structlog.get_logger(__name__)


class FrameCapture:
    """Captures frames from an RTSP stream using OpenCV VideoCapture.

    Connects to the configured RTSP URL (or a provided one) and provides
    a simple interface for reading frames with low-latency settings.
    """

    def __init__(self, rtsp_url: str = None):
        """Initialize FrameCapture with the given or configured RTSP URL.

        Args:
            rtsp_url: RTSP stream URL. Defaults to RTSP_URL from config.
        """
        self.rtsp_url = rtsp_url or RTSP_URL
        self._cap: cv2.VideoCapture = None
        self._frame_count: int = 0

        # Threading support
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_frame = None

        logger.info("FrameCapture initialized", rtsp_url=self.rtsp_url)

    def open(self) -> bool:
        """Open the video capture connection to the RTSP stream.

        Sets buffer size to 1 for low latency and configures frame dimensions.

        Returns:
            True if the connection was opened successfully, False otherwise.
        """
        logger.info("Opening video capture", rtsp_url=self.rtsp_url)
        self._cap = cv2.VideoCapture(self.rtsp_url)

        if not self._cap.isOpened():
            logger.error("Failed to connect to RTSP stream", rtsp_url=self.rtsp_url)
            return False

        # Set buffer size to 1 for low latency
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Set frame dimensions
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

        logger.info(
            "Connected to RTSP stream",
            rtsp_url=self.rtsp_url,
            resolution=f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
        )
        return True

    def set_url(self, new_url: str) -> None:
        """Update the RTSP URL dynamically and force a reconnection if changed."""
        if not new_url or new_url == self.rtsp_url:
            return
        
        logger.info("RTSP URL changing", old_url=self.rtsp_url, new_url=new_url)
        self.rtsp_url = new_url
        
        # Release current capture to force a reconnect on the next loop
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def reconnect(self) -> bool:
        """Attempt to reconnect to the RTSP stream.

        Releases the current capture (if any), waits 5 seconds, then
        attempts to re-open the connection.

        Returns:
            True if reconnection was successful, False otherwise.
        """
        logger.info("Attempting reconnection to RTSP stream...")

        # Release current capture if any
        if self._cap is not None:
            self._cap.release()
            self._cap = None

        # Wait 5 seconds before retrying
        time.sleep(5)

        # Attempt to re-open
        success = self.open()
        if success:
            logger.info("Reconnection successful")
        else:
            logger.warning("Reconnection failed, will retry on next attempt")

        return success

    def open_with_retry(self) -> bool:
        """Open the video capture with indefinite retry on failure.

        If the connection cannot be established on startup, logs an error
        and retries every 5 seconds until successful.

        Returns:
            True once the connection is established.
        """
        while True:
            if self.open():
                return True
            logger.error(
                "Cannot connect to RTSP stream, retrying",
                rtsp_url=self.rtsp_url,
                retry_seconds=5,
            )
            time.sleep(5)

    def read_frame(self):
        """Read a single frame from the capture device.

        If the capture is not open or a read fails, automatically attempts
        reconnection before returning failure.

        Returns:
            A tuple (success, frame) where success is a bool indicating
            whether the frame was read successfully, and frame is the
            numpy array of the image (or None on failure).
        """
        if self._cap is None or not self._cap.isOpened():
            logger.warning("Capture is not open, attempting reconnection")
            if not self.reconnect():
                return False, None

        success, frame = self._cap.read()
        if not success:
            logger.warning("Failed to read frame from stream, attempting reconnection")
            if self.reconnect():
                # Try reading again after successful reconnection
                success, frame = self._cap.read()
                if not success:
                    logger.warning("Failed to read frame after reconnection")
                    return False, None
            else:
                return False, None

        return success, frame

    def read_frame_with_skip(self):
        """Read a frame using frame skipping to reduce CPU usage.

        Calls grab() for (FRAME_SKIP - 1) times to advance the stream
        without decoding, then calls retrieve() once to get the actual
        frame. This ensures only every Nth frame is fully decoded.

        If any grab or retrieve fails, automatically attempts reconnection
        before returning failure.

        Returns:
            A tuple (success, frame) where success is a bool indicating
            whether the frame was retrieved successfully, and frame is the
            numpy array of the image (or None on failure).
        """
        if self._cap is None or not self._cap.isOpened():
            logger.warning("Capture is not open, attempting reconnection")
            if not self.reconnect():
                return False, None

        skip = max(1, FRAME_SKIP)

        # Grab and discard N-1 frames to advance the stream
        for _ in range(skip - 1):
            if not self._cap.grab():
                logger.warning("Failed to grab frame during skip, attempting reconnection")
                if self.reconnect():
                    return self.read_frame_with_skip()
                return False, None

        # Read (grab + retrieve) the Nth frame
        success, frame = self._cap.read()
        
        if success:
            self._frame_count += 1
        else:
            logger.warning("Failed to read frame, attempting reconnection")
            if self.reconnect():
                success, frame = self._cap.read()
                if success:
                    self._frame_count += 1
                return success, frame if success else None
            return False, None

        return success, frame

    @property
    def frame_count(self) -> int:
        """Total number of frames successfully processed (retrieved).

        Returns:
            The count of frames that have been fully decoded and returned.
        """
        return self._frame_count

    def is_opened(self) -> bool:
        """Check whether the capture is currently connected.

        Returns:
            True if the VideoCapture is open, False otherwise.
        """
        return self._cap is not None and self._cap.isOpened()

    def release(self):
        """Release the video capture resource."""
        if self._cap is not None:
            self._cap.release()
            self._cap = None
            logger.info("Video capture released")

    # ── Threaded capture interface ──────────────────────────────────────

    def start(self):
        """Start a background thread for continuous frame capture.

        The background thread will:
        1. Open the connection (retrying on failure)
        2. Continuously read frames using skip logic
        3. Store the latest frame in a thread-safe manner
        4. Reconnect on read failures
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Capture thread is already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Capture thread started")

    def stop(self):
        """Stop the background capture thread gracefully."""
        if self._thread is None or not self._thread.is_alive():
            logger.warning("Capture thread is not running")
            return

        self._stop_event.set()
        self._thread.join(timeout=10)
        self.release()
        with self._frame_lock:
            self._latest_frame = None
        logger.info("Capture thread stopped")

    def get_frame(self):
        """Return the latest captured frame in a thread-safe manner.

        Returns:
            The latest captured frame as a numpy array, or None if no
            frame is available yet.
        """
        with self._frame_lock:
            return self._latest_frame

    def frames(self):
        """Generator that yields frames from the background capture.

        Yields frames as they become available from the background thread.
        Blocks briefly between checks. Stops when the capture thread is
        signaled to stop.

        Yields:
            numpy array representing a captured and resized frame.
        """
        while not self._stop_event.is_set():
            frame = self.get_frame()
            if frame is not None:
                yield frame
            else:
                # Brief sleep to avoid busy-waiting when no frame is ready
                time.sleep(0.01)

    def _capture_loop(self):
        """Background thread loop that captures frames continuously.

        Opens the connection with retry logic, then reads frames using
        skip logic. On failure, attempts reconnection. Checks stop event
        each iteration.
        """
        # Open connection with retry loop
        while not self._stop_event.is_set():
            if self.open():
                break
            logger.warning("Failed to open capture, retrying in 5 seconds...")
            # Wait 5 seconds but check stop event periodically
            for _ in range(50):
                if self._stop_event.is_set():
                    return
                time.sleep(0.1)

        # Main capture loop
        while not self._stop_event.is_set():
            success, frame = self.read_frame_with_skip()

            if not success:
                # On read failure, attempt reconnection
                logger.warning("Frame read failed in capture loop, reconnecting...")
                while not self._stop_event.is_set():
                    if self.reconnect():
                        break
                    # Check stop event during retry wait (reconnect already waits 5s)
                    if self._stop_event.is_set():
                        return
                continue

            # Resize frame to configured dimensions
            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))

            # Store latest frame thread-safely
            with self._frame_lock:
                self._latest_frame = frame
