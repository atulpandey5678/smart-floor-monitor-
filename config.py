# config.py — All tunable parameters for Shop Floor Tracker
# All settings in one place. Do not hardcode values anywhere else.
import os
from dotenv import load_dotenv

load_dotenv()  # Load variables from .env

# ── Camera ──────────────────────────────────────────────
# Set your RTSP URL in .env or via the dashboard machine setup wizard.
# Leave empty to start in API-only mode until a camera is configured.
RTSP_URL = os.getenv("RTSP_URL", "")

FRAME_SKIP = 1  # Process every frame for maximum dashboard FPS (was 3)
FRAME_WIDTH = 1280  # Resize frame to this width before processing
FRAME_HEIGHT = 720

# ── Detection zones (pixel coordinates as fractions 0.0 to 1.0) ────
# Detection_Zone — where person detection is evaluated
DETECTION_ZONE = (0.0, 0.0, 1.0, 1.0)  # (x1, y1, x2, y2) full frame

# Badge OCR zone — where badge number appears (chest/lanyard area)
OCR_ZONE = {'x1': 0.30, 'y1': 0.10, 'x2': 0.70, 'y2': 0.55}

# ── Detection thresholds ────────────────────────────────
PERSON_CONFIDENCE_THRESHOLD = 0.60  # Min confidence for person detection
BADGE_CONFIDENCE_THRESHOLD = 0.6    # Min confidence for badge OCR
BADGE_ID_MIN_DIGITS = 4
BADGE_ID_MAX_DIGITS = 6

# ── Session rules ───────────────────────────────────────
STABLE_FRAMES_REQUIRED = 4  # Same badge ID stable for N frames to open session
GRACE_PERIOD_SECONDS = 180  # 3 minutes grace period before closing session

# ── Anti-cheat: micro-movement ──────────────────────────
MOVEMENT_THRESHOLD = 5.0           # RMS pixel diff threshold (legacy fallback)
STATIC_BADGE_TIMEOUT_SECONDS = 180 # Flag ABANDONED after this many seconds static

# ── Optical Flow Movement Detection ─────────────────────
OPTICAL_FLOW_THRESHOLD = 2.0  # Mean flow magnitude threshold (pixels/frame)
USE_OPTICAL_FLOW = True        # Use optical flow instead of RMS pixel diff

# ── OCR Temporal Smoothing ──────────────────────────────
TEMPORAL_SMOOTHING_WINDOW = 5     # Rolling window size for badge ID votes
TEMPORAL_SMOOTHING_MIN_AGREE = 3  # Minimum agreeing frames to confirm badge change

# ── Kalman Filter ───────────────────────────────────────
KALMAN_PREDICT_FRAMES = 3    # Frames to predict body position during occlusion
KALMAN_PROCESS_NOISE = 1e-2  # Process noise covariance for Kalman filter

# ── Efficiency ──────────────────────────────────────────
SHIFT_HOURS = 8  # Default shift duration for efficiency calculation

# ── Server ──────────────────────────────────────────────
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
DB_PATH = os.getenv("DB_PATH", "tracker.db")

# ── Machine identification ──────────────────────────────
MACHINE_ID = 'M-01'

# ── Machine Light Detection ─────────────────────────────
LIGHT_ZONE = (0.85, 0.05, 0.95, 0.25)  # ROI for tower light (x1, y1, x2, y2) fractional
LIGHT_HUE_GREEN = (25, 95)        # Catches dark green through bright green
LIGHT_HUE_AMBER = (10, 25)        # Orange/amber range
LIGHT_HUE_RED_LOW = (0, 10)       # Red (low hue end)
LIGHT_HUE_RED_HIGH = (160, 180)   # Red (high hue end, wraps around)
LIGHT_SATURATION_MIN = 30         # Lowered — catches dim/dark colors too
LIGHT_BRIGHTNESS_MIN = 40         # Lowered — catches dark green, dim red
LIGHT_DOMINANCE_THRESHOLD = 0.10  # Only 10% of pixels need to agree
LIGHT_STABLE_FRAMES = 3           # Frames needed for temporal stability
LIGHT_ALERT_ON_RED = True         # Generate alert on red light
LIGHT_DETECTION_ENABLED = True    # Master enable/disable

# ── Far/small light detection tuning ────────────────────
LIGHT_ANALYSIS_SIZE = 160      # Upscale the light zone so far/small lights have enough pixels
LIGHT_MIN_COLOR_PIXELS = 15    # Absolute minimum colored pixels (after upscale)
LIGHT_VIVID_SATURATION = 60    # A "real colored light" must be at least this saturated

# ── Security ─────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "")

# ── AI Integration ──────────────────────────────────────
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
