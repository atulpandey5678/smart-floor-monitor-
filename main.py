"""Shop Floor Tracker — Application Entry Point.

Initializes all components, starts the CV pipeline in a background thread,
starts the FastAPI server with uvicorn, and coordinates the main processing loop.
"""

import logging
import signal
import sys
import threading
import time

import uvicorn

from config import API_HOST, API_PORT, MACHINE_ID, DETECTION_ZONE, SHIFT_HOURS, LIGHT_DETECTION_ENABLED, LIGHT_ALERT_ON_RED
from engine.anti_cheat import AntiCheatEngine
from engine.session_manager import SessionManager
from engine.settings_manager import init_settings, get_settings
from engine.notifier import init_notifier, get_notifier
from cv_pipeline.kalman_tracker import KalmanBoxTracker

# CV imports may fail if ultralytics not fully installed
try:
    from cv_pipeline.capture import FrameCapture
    from cv_pipeline.detector import PersonDetector
    from cv_pipeline.light_detector import LightDetector
    CV_AVAILABLE = True
except ImportError as e:
    CV_AVAILABLE = False
    import logging as _log
    _log.getLogger(__name__).warning(f"CV pipeline not available: {e}. Running API-only mode.")
from db.database import init_db, Database
from db.repository import Repository
from api.routes import set_repo, set_state, get_state, set_frame_provider, set_light_detector
from api.websocket import set_state_provider

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('app.log', mode='a'),
    ]
)
logger = logging.getLogger(__name__)

# Shared state for WebSocket broadcast (protected by lock)
_state_lock = threading.Lock()
_current_state = {
    'state': 'IDLE',
    'employee_name': None,
    'active_duration_seconds': 0.0,
    'body_detected': False,
    'machine_id': MACHINE_ID,
    'session_start': None,
    'movement': 'NO_DATA',
    'alert_type': None,
    'efficiency_percent': 0.0,
    'machine_light_status': 'UNKNOWN',
    'camera_health': 'offline',
    '_frame_ts': 0.0,  # epoch time of last processed CV frame
}

_shutdown_event = threading.Event()

# Shared annotated frame for live video feed (JPEG bytes)
_frame_lock_video = threading.Lock()
_annotated_frame: bytes = b''

# Shared light detector reference for zone updates from API
_light_detector = None


def get_light_detector():
    """Get the shared LightDetector instance (for API zone updates)."""
    return _light_detector


def get_broadcast_state():
    """Get current state for WebSocket broadcast (thread-safe).

    If no CV frame has been processed in the last 3 seconds, overrides
    body_detected and movement to safe/empty defaults so stale detections
    never persist in the broadcast stream.

    Also computes camera_health based on frame age:
      - within 3s  → "online"
      - 3–10s      → "degraded"
      - >10s       → "offline"
    """
    with _state_lock:
        s = dict(_current_state)
    # Staleness guard: if the CV pipeline hasn't written a frame in 3s,
    # force detection signals off so the dashboard shows accurate state.
    age = time.time() - s.get('_frame_ts', 0.0)
    if age > 3.0:
        s['body_detected'] = False
        s['movement'] = 'NO_DATA'

    # Camera health indicator based on frame freshness
    if age <= 3.0:
        s['camera_health'] = 'online'
    elif age <= 10.0:
        s['camera_health'] = 'degraded'
    else:
        s['camera_health'] = 'offline'

    return s


def get_annotated_frame() -> bytes:
    """Get the latest annotated frame as JPEG bytes (thread-safe)."""
    with _frame_lock_video:
        return _annotated_frame


def run_cv_pipeline(capture, detector, anticheat, session_mgr, repo, light_detector):
    """Main CV processing loop — runs in a background thread.

    Processing pipeline per frame:
    1. Capture frame (with skip)
    2. Run person detection + Kalman filter smoothing
    3. Check body movement (optical flow)
    4. Feed to session manager (presence-based)
    5. Handle events (persist sessions, create alerts)
    6. Run machine light detection
    7. Update shared state for WebSocket broadcast
    """
    global _current_state

    logger.info("CV pipeline starting...")
    capture.start()

    # Initialize Kalman tracker
    body_tracker = KalmanBoxTracker()

    # Wait for first frame
    while not _shutdown_event.is_set():
        frame = capture.get_frame()
        if frame is not None:
            break
        time.sleep(0.1)

    logger.info("CV pipeline running — processing frames")
    current_session_id = None

    while not _shutdown_event.is_set():
        frame = capture.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue

        try:
            # Read live settings (takes effect without restart)
            s = get_settings()
            det = s.section("detection")
            light_cfg = s.section("light")
            shift_cfg = s.section("shifts")

            # Update live thresholds on the anti-cheat engine
            anticheat._use_optical_flow = True
            anticheat._val_min = det.get("optical_flow_threshold", 2.0)

            # Update session manager grace period and static timeout
            session_mgr._grace_period = int(det.get("grace_period_seconds", 180))
            session_mgr._static_timeout = int(det.get("static_worker_timeout_seconds", 180))

            # Update light detector thresholds
            light_detector._sat_min = int(light_cfg.get("saturation_min", 30))
            light_detector._val_min = int(light_cfg.get("brightness_min", 40))
            light_detector._vivid_sat = int(light_cfg.get("vivid_saturation", 60))
            light_detector._analysis_size = int(light_cfg.get("analysis_size", 160))
            light_detector._min_color_pixels = int(light_cfg.get("min_color_pixels", 15))
            light_detector._stable_frames = int(light_cfg.get("stable_frames", 3))
            light_detector._hue_green = (
                int(light_cfg.get("hue_green_min", 25)),
                int(light_cfg.get("hue_green_max", 95))
            )
            light_detector._hue_amber = (
                int(light_cfg.get("hue_amber_min", 10)),
                int(light_cfg.get("hue_amber_max", 25))
            )
            light_detector._hue_red_low = (
                int(light_cfg.get("hue_red_low_min", 0)),
                int(light_cfg.get("hue_red_low_max", 10))
            )
            light_detector._hue_red_high = (
                int(light_cfg.get("hue_red_high_min", 160)),
                int(light_cfg.get("hue_red_high_max", 180))
            )

            current_shift_hours = float(shift_cfg.get("default_shift_hours", SHIFT_HOURS))
            light_alert_on_red = bool(light_cfg.get("alert_on_red", True))
            raw_body_detected, raw_body_bbox = detector.detect(frame)

            # 2. Kalman filter smoothing — predicts through brief occlusions
            body_detected, body_bbox = body_tracker.update(raw_body_bbox if raw_body_detected else None)

            if body_detected and not raw_body_detected:
                logger.debug(f"Kalman predicting body at {body_bbox} (miss #{body_tracker.miss_count})")

            # 3. Movement check — ONLY when body is actually detected
            if body_detected and body_bbox:
                bx1, by1, bx2, by2 = body_bbox
                h_f, w_f = frame.shape[:2]
                cx1 = max(0, bx1 + (bx2-bx1)//4)
                cy1 = max(0, by1 + (by2-by1)//4)
                cx2 = min(w_f, bx2 - (bx2-bx1)//4)
                cy2 = min(h_f, by2 - (by2-by1)//4)
                movement_crop = frame[cy1:cy2, cx1:cx2] if cy2 > cy1 and cx2 > cx1 else None
                movement_status = anticheat.check_movement(movement_crop)
            else:
                anticheat.reset()
                movement_status = 'NO_DATA'

            badge_static = (movement_status == 'ABANDONED')

            # 4. Process frame through session manager (presence-based)
            snapshot = session_mgr.process_frame(
                body_detected=body_detected,
                badge_static=badge_static,
            )

            # 5. Handle events (session opens/closes, alerts)
            for event in snapshot.get('events', []):
                if event['type'] == 'session_opened':
                    from datetime import datetime
                    start_time = event['start_time']
                    if isinstance(start_time, str):
                        start_time = datetime.fromisoformat(start_time)
                    current_session_id = repo.create_session(
                        badge_id=event['badge_id'],
                        start_time=start_time,
                        machine_id=MACHINE_ID,
                    )
                    logger.info(
                        f"Session {current_session_id} opened (worker present)"
                    )

                elif event['type'] == 'session_closed':
                    if current_session_id:
                        from datetime import datetime
                        end_time = event['end_time']
                        if isinstance(end_time, str):
                            end_time = datetime.fromisoformat(end_time)
                        repo.close_session(
                            session_id=current_session_id,
                            end_time=end_time,
                            active_duration=event['active_duration_seconds'],
                            close_reason=event['close_reason'],
                        )
                    current_session_id = None
                    anticheat.reset()
                    body_tracker.reset()
                    logger.info(f"Session closed: {event['close_reason']}")

                elif event['type'] == 'alert_generated':
                    repo.create_alert(
                        badge_id=event['badge_id'],
                        alert_type=event['alert_type'],
                        message=event.get('message'),
                    )
                    notifier = get_notifier()
                    if notifier:
                        notifier.send_alert(
                            alert_type=event['alert_type'],
                            machine_id=MACHINE_ID,
                            message=event.get('message', ''),
                            badge_id=event['badge_id']
                        )
                    logger.warning(
                        f"Alert: {event['alert_type']}"
                    )

                        # 6. Machine light detection
            light_result = light_detector.detect(frame)
            if light_result['transition']:
                from datetime import datetime as _dt
                logger.info(
                    "Machine light: %s → %s",
                    light_result['previous'], light_result['status']
                )
                # Persist state transition
                try:
                    repo.create_machine_state_event(
                        machine_id=MACHINE_ID,
                        previous_status=light_result['previous'] or 'UNKNOWN',
                        new_status=light_result['status'],
                        timestamp=_dt.now(),
                    )
                except Exception as _e:
                    logger.error("Failed to persist machine state event: %s", _e)

                # Generate alert on RED transition
                if light_result['status'] == 'RED' and light_alert_on_red:
                    repo.create_alert(
                        badge_id='SYSTEM',
                        alert_type='machine_red_light',
                        message=f"Machine {MACHINE_ID} tower light turned RED",
                    )
                    notifier = get_notifier()
                    if notifier:
                        notifier.send_alert(
                            alert_type='machine_red_light',
                            machine_id=MACHINE_ID,
                            message=f"Machine {MACHINE_ID} tower light turned RED",
                            badge_id='SYSTEM'
                        )
                    logger.warning("Machine %s: RED light alert generated", MACHINE_ID)

            # 7. Worker presence label
            employee_name = "Worker Present" if body_detected else None

            # 8. Compute efficiency using live shift hours from settings
            active_secs = snapshot.get('active_duration_seconds', 0.0)
            shift_seconds = current_shift_hours * 3600
            efficiency_pct = round((active_secs / shift_seconds) * 100, 1) if shift_seconds > 0 else 0.0

            # 9. Update shared state
            with _state_lock:
                _current_state.update({
                    'state': snapshot['state'],
                    'employee_name': employee_name,
                    'active_duration_seconds': active_secs,
                    'body_detected': body_detected,
                    'session_start': snapshot.get('session_start'),
                    'machine_id': MACHINE_ID,
                    'movement': movement_status,
                    'alert_type': None,
                    'efficiency_percent': efficiency_pct,
                    'machine_light_status': light_result['status'],
                    '_frame_ts': time.time(),
                })

            set_state(_current_state.copy())

            # Periodically update active session duration in DB (every 10s)
            if current_session_id and snapshot.get('state') == 'ACTIVE':
                if int(active_secs) % 10 == 0 and active_secs > 0:
                    try:
                        repo.update_session(current_session_id, active_secs, 'ACTIVE')
                    except Exception:
                        pass

            # 10. Annotate frame for live video feed
            import cv2 as _cv2
            global _annotated_frame
            annotated = frame.copy()

            h, w = annotated.shape[:2]
            zx1, zy1, zx2, zy2 = DETECTION_ZONE
            _cv2.rectangle(annotated, 
                          (int(zx1*w), int(zy1*h)), (int(zx2*w), int(zy2*h)),
                          (200, 200, 200), 1)
            _cv2.putText(annotated, "Detection Zone", (int(zx1*w)+5, int(zy1*h)+15),
                        _cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

            if body_detected and body_bbox:
                bx1, by1, bx2, by2 = body_bbox
                # Different color for predicted vs measured
                box_color = (0, 255, 0) if raw_body_detected else (0, 200, 255)
                _cv2.rectangle(annotated, (bx1, by1), (bx2, by2), box_color, 2)
                label = "Person" if raw_body_detected else "Predicted"
                _cv2.putText(annotated, label, (bx1, by1-10),
                            _cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2)

            # Draw the light detection zone (magenta) with detected color + percentages
            try:
                lz = light_detector._zone
                lzx1, lzy1, lzx2, lzy2 = int(lz[0]*w), int(lz[1]*h), int(lz[2]*w), int(lz[3]*h)
                light_status = light_result['status']
                # Color the box by detected status
                lbox_color = (0, 255, 0) if light_status == 'GREEN' else \
                             (0, 165, 255) if light_status == 'AMBER' else \
                             (0, 0, 255) if light_status == 'RED' else (200, 0, 200)
                _cv2.rectangle(annotated, (lzx1, lzy1), (lzx2, lzy2), lbox_color, 2)
                _cv2.putText(annotated, f"LIGHT: {light_status}", (lzx1, max(15, lzy1-8)),
                            _cv2.FONT_HERSHEY_SIMPLEX, 0.5, lbox_color, 2)
                diag = light_result.get('diagnostics', {})
                if diag:
                    diag_txt = f"G{diag.get('green_pct',0)} A{diag.get('amber_pct',0)} R{diag.get('red_pct',0)}"
                    _cv2.putText(annotated, diag_txt, (lzx1, lzy2+15),
                                _cv2.FONT_HERSHEY_SIMPLEX, 0.4, lbox_color, 1)
            except Exception:
                pass

            state_text = snapshot['state']
            color = (0, 255, 0) if state_text == 'ACTIVE' else (0, 255, 255) if state_text == 'OPENING' else (0, 165, 255) if state_text == 'GRACE' else (0, 0, 255) if state_text == 'ABANDONED' else (180, 180, 180)
            _cv2.putText(annotated, state_text, (10, h-20),
                        _cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)

            # Efficiency overlay
            _cv2.putText(annotated, f"Eff: {efficiency_pct}%", (w-150, 25),
                        _cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            _, jpeg = _cv2.imencode('.jpg', annotated, [_cv2.IMWRITE_JPEG_QUALITY, 70])
            with _frame_lock_video:
                _annotated_frame = jpeg.tobytes()

        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            time.sleep(0.1)

    # Cleanup
    capture.stop()
    logger.info("CV pipeline stopped")


def scheduled_backup(db_path: str = 'tracker.db', backup_dir: str = 'backups',
                     keep_last: int = 14, interval_hours: float = 24.0):
    """Copy the SQLite DB to a timestamped file in backup_dir and reschedule itself.

    Runs every ``interval_hours`` hours, keeping only the ``keep_last`` most recent
    files so the folder doesn't grow unbounded.
    """
    import os, shutil
    from datetime import datetime as _dt

    try:
        os.makedirs(backup_dir, exist_ok=True)
        ts = _dt.now().strftime('%Y%m%d_%H%M%S')
        dest = os.path.join(backup_dir, f'cologic_backup_{ts}.db')
        shutil.copy2(db_path, dest)
        logger.info("Auto-backup written: %s", dest)

        # Prune old backups — keep only the N most recent
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith('cologic_backup_') and f.endswith('.db')]
        )
        for old in backups[:-keep_last]:
            try:
                os.remove(os.path.join(backup_dir, old))
                logger.info("Pruned old backup: %s", old)
            except Exception:
                pass
    except Exception as e:
        logger.error("Auto-backup failed: %s", e)

    # Reschedule
    t = threading.Timer(interval_hours * 3600, scheduled_backup,
                        kwargs=dict(db_path=db_path, backup_dir=backup_dir,
                                    keep_last=keep_last, interval_hours=interval_hours))
    t.daemon = True
    t.start()


def scheduled_report():
    """Runs daily at a specific hour to generate and email reports.

    Checks settings.notifications.report_time (e.g. "08:00"). If current time matches,
    it computes the daily report (and weekly on Mondays) and sends it.
    """
    from datetime import datetime as _dt, timedelta
    from engine.settings_manager import get_settings
    from engine.notifier import get_notifier
    from engine.report_engine import ReportEngine
    from db.database import Database
    from db.repository import Repository
    import time

    now = _dt.now()
    try:
        settings = get_settings()
        notif_cfg = settings.section("notifications")
        if notif_cfg.get("email_enabled") and notif_cfg.get("report_time"):
            target_time = notif_cfg.get("report_time") # "HH:MM"
            current_time = now.strftime("%H:%M")

            if current_time == target_time:
                # Time to send report!
                notifier = get_notifier()
                if notifier:
                    # Initialize ephemeral repo for this thread
                    db = Database()
                    repo = Repository(db)
                    shift_cfg = settings.section("shifts")
                    shift_hours = float(shift_cfg.get("default_shift_hours", 8.0))
                    engine = ReportEngine(repo, shift_hours)

                    yesterday = (now - timedelta(days=1)).date()
                    daily_rep = engine.daily_report(yesterday)
                    
                    # Generate daily HTML (very simple version for email)
                    ai_summary = engine.generate_ai_summary(daily_rep)
                    
                    html = f"<h3>Daily Report: {yesterday.isoformat()}</h3>"
                    html += ai_summary
                    html += f"<p>Total Active Hours: {daily_rep.total_active_hours}</p>"
                    html += f"<p>Total Sessions: {daily_rep.total_sessions}</p>"
                    
                    notifier.send_report(
                        subject=f"Daily Shop Floor Report ({yesterday.isoformat()})",
                        body_plain=engine._format_csv(daily_rep),
                        body_html=html
                    )

                    # If it's Monday, send weekly report for the previous week
                    if now.weekday() == 0:
                        prev_monday = yesterday - timedelta(days=6)
                        weekly_rep = engine.weekly_report(prev_monday)
                        w_html = f"<h3>Weekly Report: {prev_monday.isoformat()} to {yesterday.isoformat()}</h3>"
                        w_html += f"<p>Active Hours: {weekly_rep.total_active_hours} "
                        w_html += f"({weekly_rep.trend.active_hours_change_pct}% vs last week)</p>"
                        
                        notifier.send_report(
                            subject=f"Weekly Shop Floor Report ({prev_monday.isoformat()})",
                            body_plain=engine._format_csv(weekly_rep),
                            body_html=w_html
                        )
                    db.close()
                # Sleep a bit so we don't trigger multiple times in the same minute
                time.sleep(60)
    except Exception as e:
        logger.error("Auto-report failed: %s", e)

    # Check again in 60 seconds
    t = threading.Timer(60.0, scheduled_report)
    t.daemon = True
    t.start()


def main():
    """Application entry point."""
    logger.info("=" * 60)
    logger.info("  Cologic — Shop Floor Tracker v2.0")
    logger.info(f"  Machine: {MACHINE_ID}")
    logger.info(f"  Server:  http://{API_HOST}:{API_PORT}")
    logger.info("=" * 60)

    # ── Pre-flight validation ─────────────────────────────────
    errors = []

    # Check model file
    import os
    if not os.path.exists('yolov8n.pt'):
        errors.append("YOLO model file 'yolov8n.pt' not found. "
                       "Download it or ensure it is in the project root.")

    # Check dashboard files
    for fname in ['dashboard/index.html', 'dashboard/style.css', 'dashboard/app.js']:
        if not os.path.exists(fname):
            errors.append(f"Dashboard file missing: {fname}")

    if errors:
        for err in errors:
            logger.error("PRE-FLIGHT FAIL: %s", err)
        logger.error("Fix the above issues and restart. Exiting.")
        sys.exit(1)

    logger.info("Pre-flight checks passed ✓")

    # ── Rotate app.log to keep it clean (keep last 5000 lines) ─
    try:
        log_path = 'app.log'
        if os.path.exists(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:  # 5 MB
            with open(log_path, 'r', encoding='utf-8', errors='replace') as lf:
                lines = lf.readlines()
            with open(log_path, 'w', encoding='utf-8') as lf:
                lf.writelines(lines[-5000:])
            logger.info("app.log trimmed to last 5000 lines")
    except Exception:
        pass

    # Initialize database
    init_db()
    db = Database()
    repo = Repository(db)
    set_repo(repo)

    # Set up WebSocket state provider
    set_state_provider(get_broadcast_state)
    set_frame_provider(get_annotated_frame)

    # Initialize email notifier (reads SMTP config dynamically)
    init_notifier(get_settings())

    # Schedule daily auto-backup (first backup after 24h)
    _backup_timer = threading.Timer(
        24 * 3600, scheduled_backup,
        kwargs=dict(db_path='tracker.db', backup_dir='backups', keep_last=14, interval_hours=24.0)
    )
    _backup_timer.daemon = True
    _backup_timer.start()
    logger.info("Auto-backup scheduled every 24h → backups/ folder")

    # Start report scheduler
    _report_timer = threading.Timer(60.0, scheduled_report)
    _report_timer.daemon = True
    _report_timer.start()
    logger.info("Report scheduler started (checks every minute)")

    # Initialize CV components (only if available)
    cv_thread = None
    if CV_AVAILABLE:
        global _light_detector
        capture = FrameCapture()
        detector = PersonDetector()
        anticheat = AntiCheatEngine()
        session_mgr = SessionManager()
        light_detector = LightDetector()
        _light_detector = light_detector
        set_light_detector(light_detector)

        # Start CV pipeline in background thread
        cv_thread = threading.Thread(
            target=run_cv_pipeline,
            args=(capture, detector, anticheat, session_mgr, repo, light_detector),
            daemon=True,
        )
        cv_thread.start()
        logger.info("CV pipeline thread started")
    else:
        logger.warning("Running in API-ONLY mode (no CV pipeline). Dashboard is accessible.")

    # Signal handling for graceful shutdown
    def shutdown_handler(sig, frame):
        logger.info(f"Received signal {sig}, shutting down...")
        _shutdown_event.set()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Start FastAPI server (blocks until shutdown)
    try:
        uvicorn.run(
            "api.server:app",
            host=API_HOST,
            port=API_PORT,
            reload=False,
            log_level="warning",  # quieter uvicorn noise
        )
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_event.set()
        if cv_thread:
            cv_thread.join(timeout=5)
        db.close()
        logger.info("Application shut down")


if __name__ == "__main__":
    main()
