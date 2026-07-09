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

# ── Live_State_Cache (Cloud_Server) ─────────────────────
# Staleness interval: a machine with no valid heartbeat within this many
# seconds is marked STALE. Default 6 s, clamped to the range 2–300 s
# (Requirement 6.7).
LIVE_STATE_STALENESS_MIN_SECONDS = 2
LIVE_STATE_STALENESS_MAX_SECONDS = 300
LIVE_STATE_STALENESS_SECONDS = min(
    LIVE_STATE_STALENESS_MAX_SECONDS,
    max(
        LIVE_STATE_STALENESS_MIN_SECONDS,
        int(os.getenv("LIVE_STATE_STALENESS_SECONDS", "6")),
    ),
)
# How often the background sweeper scans the cache for newly-stale entries.
LIVE_STATE_SWEEP_INTERVAL_SECONDS = float(
    os.getenv("LIVE_STATE_SWEEP_INTERVAL_SECONDS", "1.0")
)

# ── Security ─────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "")

# ── Ingest API (Edge_Agent → Cloud_Server authentication) ──
# Long-lived secret key the Edge_Agent presents on every /api/ingest/*
# request. Kept separate from the Staff_User cookie login (SECRET_KEY).
# Stored in .env and excluded from version control.
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "")

# Maximum request body size accepted on any /api/ingest/* endpoint, in bytes.
# Inclusive of the base64-encoded Event_Image on /api/ingest/alert. Bodies
# larger than this are rejected with HTTP 413 before any parsing or
# persistence occurs (Requirement 2.9). Default 10 MB, overridable via env.
INGEST_MAX_BODY_BYTES = int(
    os.getenv("INGEST_MAX_BODY_BYTES", str(10 * 1024 * 1024))
)

# ── Sync_Client (Edge_Agent → Cloud_Server transport) ──────
# Base URL of the Cloud_Server Ingest_API. MUST be an https:// URL — the
# Sync_Client refuses to construct requests over any other scheme so RTSP
# credentials and event data are never sent in the clear (Requirement 13.5).
# Read from the git-excluded .env on the on-site machine (Requirement 14.4).
CLOUD_SERVER_BASE_URL = os.getenv("CLOUD_SERVER_BASE_URL", "")

# Per-attempt transmission timeout. A delivery attempt that receives no
# acknowledgment within this many seconds is treated as failed (Requirement 4.1).
SYNC_TRANSMISSION_TIMEOUT_SECONDS = float(
    os.getenv("SYNC_TRANSMISSION_TIMEOUT_SECONDS", "10")
)

# Number of consecutive failed transmission attempts after which the
# Cloud_Server is considered unreachable, at which point new durable events go
# straight to the Offline_Queue (Requirement 4.1).
SYNC_UNREACHABLE_FAILURE_THRESHOLD = int(
    os.getenv("SYNC_UNREACHABLE_FAILURE_THRESHOLD", "3")
)

# Confirmation deadline for a single durable delivery. If no confirming HTTP
# 200 arrives within this many seconds, the flusher treats the attempt as
# failed and retains the event for retry (Requirement 5.5).
SYNC_ACK_TIMEOUT_SECONDS = float(os.getenv("SYNC_ACK_TIMEOUT_SECONDS", "30"))

# Machine_Metadata poll interval, clamped to the range 10–600 s
# (Requirement 7.3). Default 60 s.
METADATA_POLL_MIN_SECONDS = 10
METADATA_POLL_MAX_SECONDS = 600
METADATA_POLL_INTERVAL_SECONDS = min(
    METADATA_POLL_MAX_SECONDS,
    max(
        METADATA_POLL_MIN_SECONDS,
        int(os.getenv("METADATA_POLL_INTERVAL_SECONDS", "60")),
    ),
)

# Upper bound on how long the durable-event flusher waits before retrying a
# failed/unconfirmed head event. Requirement 4.5/5.5 require retrying the
# failed head at intervals no greater than 60 s.
SYNC_FLUSH_RETRY_MAX_SECONDS = min(
    60.0, float(os.getenv("SYNC_FLUSH_RETRY_MAX_SECONDS", "60"))
)

# How often the flusher wakes to check for newly queued work while the queue is
# empty (idle poll). Small so freshly submitted events transmit promptly.
SYNC_FLUSH_IDLE_INTERVAL_SECONDS = float(
    os.getenv("SYNC_FLUSH_IDLE_INTERVAL_SECONDS", "1")
)

# Exponential reconnect backoff for the metadata poller when the Cloud_Server
# is unreachable: delay_n = min(initial × 2^(n−1), max) (Requirement 12.3).
SYNC_RECONNECT_BACKOFF_INITIAL_SECONDS = float(
    os.getenv("SYNC_RECONNECT_BACKOFF_INITIAL_SECONDS", "1")
)
SYNC_RECONNECT_BACKOFF_MAX_SECONDS = float(
    os.getenv("SYNC_RECONNECT_BACKOFF_MAX_SECONDS", "60")
)

# ── Edge live status: Heartbeat + Snapshot_Thumbnail ───────
# Cadence and sizing for the per-frame live-status adapters (tasks 13.1/13.2).
# These govern how the Edge_Agent turns the Session_Manager snapshot + camera
# frame into best-effort Heartbeats and reduced-resolution Snapshot_Thumbnails.

# Heartbeat is sent within this many seconds of a Session_Manager state change
# (Requirement 6.1). Frames are processed far faster than this bound, so a
# state change is reported on the next processed frame.
EDGE_HEARTBEAT_STATE_CHANGE_SECONDS = 0.5

# While a session is active, a Heartbeat is sent every EDGE_HEARTBEAT_INTERVAL
# seconds with EDGE_HEARTBEAT_TOLERANCE seconds of permitted jitter
# (Requirement 6.2: 2 s ± 500 ms).
EDGE_HEARTBEAT_INTERVAL_SECONDS = float(
    os.getenv("EDGE_HEARTBEAT_INTERVAL_SECONDS", "2.0")
)
EDGE_HEARTBEAT_TOLERANCE_SECONDS = float(
    os.getenv("EDGE_HEARTBEAT_TOLERANCE_SECONDS", "0.5")
)

# Camera-health thresholds derived from the age of the most recent frame
# (Requirement 6.3): HEALTHY when age ≤ 2 s, DEGRADED when 2 s < age ≤ 10 s,
# FAILED when age > 10 s or the stream is disconnected.
EDGE_CAMERA_HEALTHY_MAX_AGE_SECONDS = 2.0
EDGE_CAMERA_DEGRADED_MAX_AGE_SECONDS = 10.0

# Snapshot_Thumbnail push cadence while a machine is active (Requirement 9.1:
# between 2 and 5 seconds). Clamped to that range.
EDGE_SNAPSHOT_MIN_INTERVAL_SECONDS = 2.0
EDGE_SNAPSHOT_MAX_INTERVAL_SECONDS = 5.0
EDGE_SNAPSHOT_INTERVAL_SECONDS = min(
    EDGE_SNAPSHOT_MAX_INTERVAL_SECONDS,
    max(
        EDGE_SNAPSHOT_MIN_INTERVAL_SECONDS,
        float(os.getenv("EDGE_SNAPSHOT_INTERVAL_SECONDS", "3.0")),
    ),
)

# Maximum dimension (width or height, in pixels) of a Snapshot_Thumbnail
# (Requirement 9.2). Each dimension of the produced thumbnail is ≤ this cap and
# ≤ the corresponding source dimension. Also used to bound the annotated
# alert Event_Image is left full-resolution — only the live-view thumbnail is
# reduced.
EDGE_SNAPSHOT_MAX_DIMENSION = int(os.getenv("EDGE_SNAPSHOT_MAX_DIMENSION", "320"))

# JPEG quality (0-100) for encoded thumbnails and annotated Event_Images.
EDGE_JPEG_QUALITY = int(os.getenv("EDGE_JPEG_QUALITY", "80"))

# ── AI Integration ──────────────────────────────────────
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")

# ── Object Store (Google Cloud Storage) ─────────────────
# Cloud_Server event-image storage. Credentials/config come from the
# environment (git-excluded .env), never hardcoded. When the bucket is
# unset or the google-cloud-storage package is unavailable, the Cloud_Server
# falls back to an in-memory Object_Store (development / tests only).
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
# Path to a GCP service-account JSON key. If empty, the client uses
# Application Default Credentials (ADC).
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
