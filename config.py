# config.py — All tunable parameters for Shop Floor Tracker
# All settings in one place. Do not hardcode values anywhere else.
import os
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env

# ── Camera ──────────────────────────────────────────────
RTSP_URL = 'rtsp://admin:123456@192.168.0.36:554/Streaming/Channels/101'
# For testing with a local video file:
# RTSP_URL = 'test_video.mp4'

FRAME_SKIP = 3  # Process every Nth frame (3 = ~8 FPS on CPU)
FRAME_WIDTH = 1280  # Resize frame to this width before processing
FRAME_HEIGHT = 720

# ── Detection zones (pixel coordinates as fractions 0.0 to 1.0) ────
# Detection_Zone — where person detection is evaluated
DETECTION_ZONE = (0.0, 0.0, 1.0, 1.0)  # (x1, y1, x2, y2) full frame

# Badge OCR zone — where badge number appears (chest/lanyard area)
OCR_ZONE = {'x1': 0.30, 'y1': 0.10, 'x2': 0.70, 'y2': 0.55}

# ── Detection thresholds ────────────────────────────────
PERSON_CONFIDENCE_THRESHOLD = 0.60  # Min confidence for person detection (0.6 filters furniture/background)
BADGE_CONFIDENCE_THRESHOLD = 0.6  # Min confidence for badge OCR
BADGE_ID_MIN_DIGITS = 4
BADGE_ID_MAX_DIGITS = 6

# ── Session rules ───────────────────────────────────────
STABLE_FRAMES_REQUIRED = 4  # Same badge ID stable for N frames to open session (4 = production default)
GRACE_PERIOD_SECONDS = 180  # 3 minutes grace period before closing session

# ── Anti-cheat: micro-movement ──────────────────────────
MOVEMENT_THRESHOLD = 5.0  # RMS pixel diff threshold (legacy, used as fallback)
STATIC_BADGE_TIMEOUT_SECONDS = 180  # Flag ABANDONED after this many seconds static

# ── Optical Flow Movement Detection ─────────────────────
OPTICAL_FLOW_THRESHOLD = 2.0  # Mean flow magnitude threshold (pixels/frame)
USE_OPTICAL_FLOW = True  # Use optical flow instead of RMS pixel diff

# ── OCR Temporal Smoothing ──────────────────────────────
TEMPORAL_SMOOTHING_WINDOW = 5  # Rolling window size for badge ID votes
TEMPORAL_SMOOTHING_MIN_AGREE = 3  # Minimum agreeing frames to confirm badge change

# ── Kalman Filter ───────────────────────────────────────
KALMAN_PREDICT_FRAMES = 3  # Frames to predict body position during occlusion
KALMAN_PROCESS_NOISE = 1e-2  # Process noise covariance for Kalman filter

# ── Efficiency ──────────────────────────────────────────
SHIFT_HOURS = 8  # Default shift duration for efficiency calculation

# ── Server ──────────────────────────────────────────────
API_HOST = '127.0.0.1'  # Localhost only
API_PORT = 8000
DB_PATH = 'tracker.db'  # SQLite database file path

# ── Machine identification ──────────────────────────────
MACHINE_ID = 'M-01'

# ── Machine Light Detection ─────────────────────────────
LIGHT_ZONE = (0.85, 0.05, 0.95, 0.25)  # ROI for tower light (x1, y1, x2, y2) fractional
LIGHT_HUE_GREEN = (25, 95)       # Expanded: catches dark green (25) through bright green (95)
LIGHT_HUE_AMBER = (10, 25)       # Orange/amber range
LIGHT_HUE_RED_LOW = (0, 10)      # Red (low hue end)
LIGHT_HUE_RED_HIGH = (160, 180)  # Red (high hue end, wraps around) — expanded from 170 to 160
LIGHT_SATURATION_MIN = 30        # Lowered from 80 — catches dim/dark colors too
LIGHT_BRIGHTNESS_MIN = 40        # Lowered from 150 — catches dark green, dim red, any visible color
LIGHT_DOMINANCE_THRESHOLD = 0.10 # Lowered from 0.20 — only 10% of pixels need to agree
LIGHT_STABLE_FRAMES = 3          # Frames needed for temporal stability
LIGHT_ALERT_ON_RED = True        # Generate alert on red light
LIGHT_DETECTION_ENABLED = True   # Master enable/disable

# ── Far/small light detection tuning ────────────────────
LIGHT_ANALYSIS_SIZE = 160        # Upscale the light zone so far/small lights have enough pixels
LIGHT_MIN_COLOR_PIXELS = 15      # Absolute minimum colored pixels (after upscale) to register a color
LIGHT_VIVID_SATURATION = 60      # A "real colored light" must be at least this saturated

# ── AI Integration ──────────────────────────────────────
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
