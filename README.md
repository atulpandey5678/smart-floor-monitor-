# Cologic Shop Floor Tracker

A production-ready, edge-cloud split system for monitoring worker presence at machine stations using IP cameras. The system uses YOLOv8-nano for person detection, PaddleOCR for badge reading, and enforces anti-cheat rules (co-presence and micro-movement validation). Features resilient edge agents with offline queueing and a centralized cloud server for dashboard access and data aggregation.

## 🏗️ Architecture

- **Edge Agents**: Run on-site at each factory, process camera feeds, queue events locally
- **Cloud Server**: Centralized dashboard and API hosted on GCP
- **Offline-First**: Edge agents continue operating during network/cloud outages
- **HTTPS**: Secure TLS communication between edge and cloud

## 📚 Documentation

### Deployment Guides
- **[Complete GCP Deployment Guide](./docs/GCP_DEPLOYMENT_GUIDE.md)** — Comprehensive step-by-step guide (30-45 min)
- **[GCP Quick Start](./docs/GCP_QUICK_START.md)** — Fast deployment for experienced users (15-20 min)
- **[Deployment Checklist](./docs/DEPLOYMENT_CHECKLIST.md)** — Print-friendly checklist with sign-offs
- **[Documentation Index](./docs/README.md)** — Complete documentation overview

### Quick Links
- [Edge Agent Installation (Windows)](./deploy/edge/README.md)
- [Edge Agent Installation (Linux)](./deploy/edge/README.md)
- [Cloud Server Deployment](./deploy/cloud/README.md)
- [Automated Deployment Script](./scripts/deploy-gcp.sh)

## 💰 Cost Estimate

**Monthly GCP Costs** (for cloud server):
- VM (e2-medium): ~$24/month
- Static IP: ~$7/month
- Cloud Storage (~10 GB): ~$0.20/month
- Bandwidth (~50 GB/month): ~$6/month
- **Total**: ~$35-50/month

**Edge Agents**: No cloud costs — run on existing factory hardware

## System Requirements

### Cloud Server (GCP)
- **VM**: e2-medium or higher (2 vCPUs, 4 GB RAM)
- **OS**: Ubuntu 22.04 LTS
- **Disk**: 50 GB minimum
- **Database**: SQLite (WAL mode)
- **Storage**: Google Cloud Storage for images

### Edge Agent (Factory Site)
- **OS**: Windows 10/11 or Linux (Ubuntu 20.04+)
- **Python**: 3.10+
- **CPU**: 4+ cores (Intel i5 or equivalent)
- **RAM**: 8 GB minimum recommended
- **GPU**: Optional (NVIDIA for accelerated CV)
- **Camera**: IP camera with RTSP stream support
- **Network**: HTTPS connectivity to cloud server

## 🚀 Quick Start

### For Cloud Server Deployment
See the **[GCP Quick Start Guide](./docs/GCP_QUICK_START.md)** or use the automated script:

```bash
cd scripts
./deploy-gcp.sh
```

### For Edge Agent Installation (Windows)
```powershell
# Clone repository
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-

# Configure environment
cp .env.example .env
# Edit .env with your cloud server URL and API key

# Configure cameras
# Edit camera_config.json with your RTSP URLs

# Install as Windows service (run as Administrator)
cd deploy\edge
.\install-windows-service.ps1
```

### For Edge Agent Installation (Linux)
```bash
# Clone repository
git clone https://github.com/atulpandey5678/smart-floor-monitor-.git
cd smart-floor-monitor-

# Install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your cloud server URL and API key

# Configure cameras
# Edit camera_config.json with your RTSP URLs

# Install as systemd service
sudo cp deploy/edge/cologic-edge-agent.service /etc/systemd/system/
sudo systemctl enable cologic-edge-agent
sudo systemctl start cologic-edge-agent
```

### For Local Development/Testing
```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your camera RTSP URL

# 4. Run the application
python main.py

# 5. Open dashboard
# http://127.0.0.1:8000
```

## Configuration Guide

All parameters are in `config.py`. Sensitive values (RTSP_URL, API keys) are loaded from `.env` — see `.env.example` for the full list.

### Camera Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `RTSP_URL` | `rtsp://localhost:554/stream` | RTSP stream URL of the IP camera (set in `.env`). Can also be a local video file path for testing. |
| `FRAME_SKIP` | `3` | Process every Nth frame. Higher = less CPU, lower detection rate. At 3, expect ~8 FPS processing. |
| `FRAME_WIDTH` | `1280` | Resize captured frame width before processing. |
| `FRAME_HEIGHT` | `720` | Resize captured frame height before processing. |

### Detection Zones

| Parameter | Default | Description |
|-----------|---------|-------------|
| `DETECTION_ZONE` | `(0.05, 0.05, 0.95, 0.95)` | Person detection region as (x1, y1, x2, y2) fractions (0.0–1.0) of frame dimensions. |
| `OCR_ZONE` | `{'x1': 0.30, 'y1': 0.10, 'x2': 0.70, 'y2': 0.55}` | Badge OCR region as fractions. Corresponds to chest/lanyard area. |

### Detection Thresholds

| Parameter | Default | Description |
|-----------|---------|-------------|
| `PERSON_CONFIDENCE_THRESHOLD` | `0.5` | Minimum confidence score for person detection. Lower = more sensitive, more false positives. |
| `BADGE_CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence for badge OCR read. |
| `BADGE_ID_MIN_DIGITS` | `4` | Minimum digits in a valid badge ID. |
| `BADGE_ID_MAX_DIGITS` | `6` | Maximum digits in a valid badge ID. |

### Session Rules

| Parameter | Default | Description |
|-----------|---------|-------------|
| `STABLE_FRAMES_REQUIRED` | `4` | Same badge ID must be read for N consecutive frames to open a session. |
| `GRACE_PERIOD_SECONDS` | `180` | Seconds to wait before closing a session when detection is lost (3 minutes). |

### Anti-Cheat Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MOVEMENT_THRESHOLD` | `5.0` | Pixel difference threshold for badge movement detection. |
| `STATIC_BADGE_TIMEOUT_SECONDS` | `180` | Seconds a badge can remain static before flagging as ABANDONED (3 minutes). |

### Server Settings

| Parameter | Default | Description |
|-----------|---------|-------------|
| `API_HOST` | `'127.0.0.1'` | Server bind address. Localhost only for security. |
| `API_PORT` | `8000` | HTTP/WebSocket server port. |
| `DB_PATH` | `'tracker.db'` | SQLite database file path. |
| `MACHINE_ID` | `'M-01'` | Identifier displayed on dashboard for this station. |

## Camera Setup

### RTSP URL Format

Most IP cameras use one of these formats:

```
rtsp://<username>:<password>@<ip>:<port>/stream1
rtsp://<username>:<password>@<ip>:<port>/h264/ch1/main/av_stream
rtsp://<username>:<password>@<ip>:<port>/Streaming/Channels/101
```

Check your camera's documentation for the exact path. Common defaults:
- Port: 554
- Username/password: admin/admin or admin/password

### Recommended Camera Placement

- **Distance**: 1.5–2.5 meters from the worker station
- **Angle**: Slightly above eye level, angled downward ~15°
- **Field of view**: Should capture full torso (badge area) and upper body
- **Lighting**: Ensure consistent lighting on the badge; avoid backlighting

### Badge Design Tips

- Print badge numbers in **bold, high-contrast font** (black on white, minimum 48pt)
- Use digits only (4–6 characters): e.g., `1234`, `00567`, `987654`
- Avoid decorative fonts, borders that touch digits, or reflective surfaces
- Position badge at chest level on a lanyard or clip

## Architecture Overview

```
Browser (http://127.0.0.1:8000)
    ↕ HTTP REST + WebSocket
FastAPI Server (api/)
    ↕
Core Engine (engine/)
  • Session Manager — state machine (IDLE→OPENING→ACTIVE→GRACE→CLOSED)
  • Anti-Cheat Engine — co-presence + micro-movement rules
  • SQLite Database (db/) — employees, sessions, alerts
    ↕
CV Pipeline (cv_pipeline/)
  • Frame Capture — RTSP stream via OpenCV
  • Person Detector — YOLOv8-nano (CPU)
  • Badge Reader — PaddleOCR
```

### Session States

| State | Meaning |
|-------|---------|
| IDLE | No worker detected |
| OPENING | Badge+body detected, waiting for stability (4 frames) |
| ACTIVE | Session running, time accumulating |
| GRACE | Detection lost, waiting up to 3 min for return |
| EXCEPTION | Body lost but badge still visible (possible fraud) |
| ABANDONED | Badge static for 3+ minutes (possible taped badge) |
| CLOSED | Session finalized and recorded |

## API Endpoints

All endpoints are served at `http://127.0.0.1:8000`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Current live state (session info, detection indicators) |
| GET | `/api/sessions/today` | All sessions for the current day |
| GET | `/api/sessions/history` | Sessions from the last 7 days |
| GET | `/api/alerts` | All unresolved alerts |
| POST | `/api/alerts/{id}/resolve` | Mark an alert as resolved |
| GET | `/api/employees` | All registered employees |
| POST | `/api/employees` | Register/update employee (body: `{"badge_id": "1234", "name": "John"}`) |
| WS | `/ws` | WebSocket for real-time state updates (1-second interval) |

### Example: Register an Employee

```bash
curl -X POST http://127.0.0.1:8000/api/employees \
  -H "Content-Type: application/json" \
  -d '{"badge_id": "1234", "name": "Jane Smith"}'
```

### Example: Get Current Status

```bash
curl http://127.0.0.1:8000/api/status
```

Response:
```json
{
  "state": "ACTIVE",
  "badge_id": "1234",
  "employee_name": "Jane Smith",
  "active_duration_seconds": 3421.5,
  "body_detected": true,
  "badge_detected": true
}
```

## Troubleshooting

### Camera Not Connecting

| Symptom | Solution |
|---------|----------|
| "Connection failed" in logs, retrying every 5s | Verify RTSP URL is correct. Test with VLC: Media → Open Network Stream → paste URL. |
| Connection drops frequently | Check network stability. Use wired connection if possible. Some cameras have a connection limit — close other RTSP clients. |
| Black frames / no data | Camera may require specific codec settings. Try adding `?tcp` to the RTSP URL for TCP transport. |

### OCR Accuracy Issues

| Symptom | Solution |
|---------|----------|
| Badge not being read | Ensure badge is well-lit, flat (not curled), and within the `OCR_ZONE`. Increase `OCR_ZONE` area if badge is outside it. |
| Wrong numbers read | Increase `BADGE_CONFIDENCE_THRESHOLD` to 0.7–0.8. Ensure badge font is clean and high-contrast. |
| Session keeps re-opening | Badge read is unstable. Increase `STABLE_FRAMES_REQUIRED` to 5–6, or improve badge visibility. |

### High CPU Usage

| Symptom | Solution |
|---------|----------|
| CPU at 100% constantly | Increase `FRAME_SKIP` (e.g., from 3 to 5 or 6). This reduces processing to ~5 FPS but lowers load. |
| Slow response times | Reduce `FRAME_WIDTH`/`FRAME_HEIGHT` (e.g., 960×540). Smaller frames = faster inference. |
| System unresponsive | Ensure no other heavy applications are running. YOLOv8-nano + PaddleOCR need ~4 GB RAM combined. |

### Dashboard Issues

| Symptom | Solution |
|---------|----------|
| Dashboard won't load | Verify `python main.py` is running. Check that port 8000 is not used by another process. |
| "Disconnected" shown on dashboard | WebSocket lost connection. It will auto-reconnect. Check if the Python process crashed. |
| No live updates | Ensure camera is connected and processing frames. Check terminal output for errors. |

### Database Issues

| Symptom | Solution |
|---------|----------|
| "Database locked" errors | Only one instance of the application should run at a time. Check for duplicate processes. |
| Missing historical data | The `tracker.db` file stores all data. Back it up periodically. Do not delete it. |

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test module
pytest tests/test_session_manager.py -v

# Run with coverage
pytest tests/ --cov=. --cov-report=term-missing
```

## File Structure

```
├── config.py                 # All tunable parameters
├── main.py                   # Application entry point
├── requirements.txt          # Python dependencies
├── cv_pipeline/
│   ├── capture.py            # RTSP frame capture with reconnection
│   ├── detector.py           # YOLOv8-nano person detection
│   └── ocr.py               # PaddleOCR badge reading
├── engine/
│   ├── models.py             # Data models and enums
│   ├── session_manager.py    # Session state machine
│   └── anti_cheat.py         # Co-presence and movement rules
├── db/
│   ├── database.py           # SQLite setup and schema
│   └── repository.py         # CRUD operations
├── api/
│   ├── server.py             # FastAPI app setup
│   ├── routes.py             # REST endpoints
│   └── websocket.py          # WebSocket handler
├── dashboard/
│   ├── index.html            # Single-page dashboard
│   ├── style.css             # Dark theme styles
│   └── app.js                # Frontend logic
└── tests/                    # Unit and integration tests
```
