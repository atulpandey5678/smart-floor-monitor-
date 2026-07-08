"""Pipeline Orchestrator — manages multiple concurrent CV pipeline threads.

Handles lifecycle (start, stop, restart), crash isolation, status tracking,
auto-restart with backoff, configurable concurrency limits, and configuration
hot-reload with validation.

Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 1.3, 24.1, 24.2, 24.3
"""

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

import structlog

logger = structlog.get_logger(__name__)


# ── Detection Parameter Validation ───────────────────────────────────

# Keys that represent confidence thresholds (must be 0.1–1.0)
_CONFIDENCE_KEYS = {
    "person_confidence_threshold",
    "confidence_threshold",
}

# Keys that represent zone coordinates (must be 0.0–1.0)
_ZONE_KEYS = {
    "detection_zone",
    "ocr_zone",
    "light_zone",
}


def validate_detection_params(params: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate detection parameter ranges before applying hot-reload.

    Checks:
      - Confidence thresholds must be between 0.1 and 1.0 (inclusive)
      - Zone coordinates must be between 0.0 and 1.0 (inclusive)

    Args:
        params: Dict of parameter names to values to validate.

    Returns:
        (True, "") if all parameters are valid.
        (False, "error message") if any parameter is out of range.
    """
    for key, value in params.items():
        # Validate confidence thresholds
        if key in _CONFIDENCE_KEYS:
            if not isinstance(value, (int, float)):
                return False, f"Parameter '{key}' must be a number, got {type(value).__name__}"
            if value < 0.1 or value > 1.0:
                return False, f"Parameter '{key}' must be between 0.1 and 1.0, got {value}"

        # Validate zone coordinates (list/tuple of floats)
        elif key in _ZONE_KEYS:
            if value is None:
                continue  # None zones are allowed (disables the zone)
            if not isinstance(value, (list, tuple)):
                return False, f"Parameter '{key}' must be a list or tuple of coordinates, got {type(value).__name__}"
            for i, coord in enumerate(value):
                if not isinstance(coord, (int, float)):
                    return False, f"Parameter '{key}[{i}]' must be a number, got {type(coord).__name__}"
                if coord < 0.0 or coord > 1.0:
                    return False, f"Parameter '{key}[{i}]' must be between 0.0 and 1.0, got {coord}"

    return True, ""


class PipelineStatus(str, Enum):
    """Operational status of a single CV pipeline."""
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STARTING = "starting"


@dataclass
class PipelineInstance:
    """Holds runtime state for a single CV pipeline."""
    machine_id: str
    machine_config: Dict[str, Any]
    thread: Optional[threading.Thread] = None
    status: PipelineStatus = PipelineStatus.STOPPED
    restart_count: int = 0
    last_error: Optional[str] = None
    last_frame_time: float = 0.0
    stop_event: threading.Event = field(default_factory=threading.Event)
    # Per-pipeline components (set during start)
    components: Dict[str, Any] = field(default_factory=dict)


class PipelineOrchestrator:
    """Manages lifecycle of multiple CV_Pipeline threads.

    Thread-safe: can be called from main thread and API handlers concurrently.

    Args:
        max_pipelines: Maximum concurrent pipelines allowed (default 8).
        restart_delay: Seconds to wait before auto-restarting a crashed pipeline (default 10).
        max_restart_attempts: Max consecutive restart attempts before marking failed (default 3).
        pipeline_factory: Callable that creates and runs a pipeline given
            (machine_config, stop_event, instance). If None, uses a default
            that requires cv_pipeline components to be available.
    """

    DEFAULT_MAX_PIPELINES = 8
    DEFAULT_RESTART_DELAY = 10.0
    DEFAULT_MAX_RESTART_ATTEMPTS = 3

    def __init__(
        self,
        max_pipelines: int = DEFAULT_MAX_PIPELINES,
        restart_delay: float = DEFAULT_RESTART_DELAY,
        max_restart_attempts: int = DEFAULT_MAX_RESTART_ATTEMPTS,
        pipeline_factory: Optional[Callable] = None,
    ):
        self._max_pipelines = max_pipelines
        self._restart_delay = restart_delay
        self._max_restart_attempts = max_restart_attempts
        self._pipeline_factory = pipeline_factory or self._default_pipeline_factory

        # Thread-safe access to pipelines dict
        self._lock = threading.Lock()
        self._pipelines: Dict[str, PipelineInstance] = {}

        # Global orchestrator shutdown flag
        self._shutdown_event = threading.Event()

        logger.info(
            "PipelineOrchestrator initialized (max_pipelines=%d, restart_delay=%.1fs, max_restarts=%d)",
            self._max_pipelines, self._restart_delay, self._max_restart_attempts,
        )

    @property
    def max_pipelines(self) -> int:
        """Maximum allowed concurrent pipelines."""
        return self._max_pipelines

    def start_pipeline(self, machine_config: Dict[str, Any]) -> bool:
        """Start a CV pipeline for the given machine configuration.

        Args:
            machine_config: Dict containing at minimum 'machine_id' and 'rtsp_url'.
                May also include 'detection_zone', 'person_confidence_threshold',
                'light_zone', 'display_name', etc.

        Returns:
            True if pipeline was started successfully, False if limit reached
            or pipeline already running.
        """
        machine_id = machine_config["machine_id"]

        with self._lock:
            # Check if already running
            if machine_id in self._pipelines:
                existing = self._pipelines[machine_id]
                if existing.status in (PipelineStatus.RUNNING, PipelineStatus.STARTING):
                    logger.warning("Pipeline for %s already running, ignoring start request", machine_id)
                    return False

            # Check concurrency limit
            active_count = sum(
                1 for p in self._pipelines.values()
                if p.status in (PipelineStatus.RUNNING, PipelineStatus.STARTING, PipelineStatus.RECONNECTING)
            )
            if active_count >= self._max_pipelines:
                logger.error(
                    "Cannot start pipeline for %s: max concurrent pipelines reached (%d/%d)",
                    machine_id, active_count, self._max_pipelines,
                )
                return False

            # Create or reset pipeline instance
            instance = PipelineInstance(
                machine_id=machine_id,
                machine_config=machine_config,
                status=PipelineStatus.STARTING,
                stop_event=threading.Event(),
            )
            self._pipelines[machine_id] = instance

        # Start pipeline in a dedicated daemon thread
        thread = threading.Thread(
            target=self._run_pipeline_wrapper,
            args=(instance,),
            name=f"cv-pipeline-{machine_id}",
            daemon=True,
        )
        instance.thread = thread
        thread.start()

        logger.info("Pipeline started for machine %s", machine_id)
        return True

    def stop_pipeline(self, machine_id: str, timeout: float = 5.0) -> bool:
        """Gracefully stop a pipeline for the given machine.

        Args:
            machine_id: The machine whose pipeline to stop.
            timeout: Maximum seconds to wait for the thread to join (default 5s).

        Returns:
            True if pipeline was found and stop was signaled, False if not found.
        """
        with self._lock:
            instance = self._pipelines.get(machine_id)
            if instance is None:
                logger.warning("No pipeline found for machine %s", machine_id)
                return False

        # Signal the pipeline to stop
        instance.stop_event.set()

        # Wait for thread to finish
        if instance.thread and instance.thread.is_alive():
            instance.thread.join(timeout=timeout)
            if instance.thread.is_alive():
                logger.warning(
                    "Pipeline thread for %s did not stop within %.1fs timeout",
                    machine_id, timeout,
                )

        with self._lock:
            instance.status = PipelineStatus.STOPPED
            instance.restart_count = 0

        logger.info("Pipeline stopped for machine %s", machine_id)
        return True

    def restart_pipeline(self, machine_id: str) -> bool:
        """Restart a pipeline: stop then start with existing config.

        Args:
            machine_id: The machine whose pipeline to restart.

        Returns:
            True if restart was initiated, False if machine not found.
        """
        with self._lock:
            instance = self._pipelines.get(machine_id)
            if instance is None:
                logger.warning("Cannot restart pipeline for %s: not found", machine_id)
                return False
            config = instance.machine_config.copy()

        # Stop existing
        self.stop_pipeline(machine_id)
        # Start fresh
        return self.start_pipeline(config)

    def get_status(self, machine_id: str) -> Optional[Dict[str, Any]]:
        """Get the status of a specific pipeline.

        Returns:
            Dict with status info or None if machine not found.
        """
        with self._lock:
            instance = self._pipelines.get(machine_id)
            if instance is None:
                return None
            return self._instance_to_status(instance)

    def get_all_statuses(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all managed pipelines.

        Returns:
            Dict mapping machine_id to status dict.
        """
        with self._lock:
            return {
                mid: self._instance_to_status(inst)
                for mid, inst in self._pipelines.items()
            }

    def stop_all(self, timeout: float = 10.0) -> None:
        """Stop all running pipelines. Used during graceful shutdown.

        Args:
            timeout: Maximum total seconds to wait for all pipelines to stop.
        """
        self._shutdown_event.set()

        with self._lock:
            machine_ids = list(self._pipelines.keys())

        # Signal all pipelines to stop
        for mid in machine_ids:
            with self._lock:
                instance = self._pipelines.get(mid)
            if instance:
                instance.stop_event.set()

        # Wait for all threads
        deadline = time.time() + timeout
        for mid in machine_ids:
            with self._lock:
                instance = self._pipelines.get(mid)
            if instance and instance.thread and instance.thread.is_alive():
                remaining = max(0.1, deadline - time.time())
                instance.thread.join(timeout=remaining)

        # Mark all as stopped
        with self._lock:
            for instance in self._pipelines.values():
                instance.status = PipelineStatus.STOPPED

        logger.info("All pipelines stopped (%d total)", len(machine_ids))

    def update_pipeline_config(self, machine_id: str, updates: Dict[str, Any]) -> Tuple[bool, str]:
        """Hot-reload configuration for a running pipeline.

        Validates incoming parameters, logs previous and new values for changed
        parameters, then updates the machine_config stored on the instance.
        The pipeline loop reads these updated values on its next iteration,
        applying changes within one frame cycle (~5 seconds max).

        Args:
            machine_id: The machine to update.
            updates: Dict of config fields to update.

        Returns:
            (True, "") if pipeline found and config updated successfully.
            (False, "error message") if validation fails or pipeline not found.
        """
        # Validate parameters before applying
        valid, error_msg = validate_detection_params(updates)
        if not valid:
            logger.warning(
                "Config validation failed for %s: %s", machine_id, error_msg
            )
            return False, error_msg

        with self._lock:
            instance = self._pipelines.get(machine_id)
            if instance is None:
                return False, f"No pipeline found for machine '{machine_id}'"

            # Log previous and new values for changed parameters
            for key, new_value in updates.items():
                previous_value = instance.machine_config.get(key)
                if previous_value != new_value:
                    logger.info(
                        "Hot-reload config for %s: %s changed from %r to %r",
                        machine_id, key, previous_value, new_value,
                    )

            instance.machine_config.update(updates)
            logger.info(
                "Pipeline config updated for %s: %s", machine_id, list(updates.keys())
            )
            return True, ""

    def get_pipeline_instance(self, machine_id: str) -> Optional[PipelineInstance]:
        """Get the raw PipelineInstance for a machine (for component access).

        Returns:
            PipelineInstance or None if not found.
        """
        with self._lock:
            return self._pipelines.get(machine_id)

    # ── Internal Methods ─────────────────────────────────────────

    def _instance_to_status(self, instance: PipelineInstance) -> Dict[str, Any]:
        """Convert a PipelineInstance to a serializable status dict."""
        return {
            "machine_id": instance.machine_id,
            "status": instance.status.value,
            "restart_count": instance.restart_count,
            "last_error": instance.last_error,
            "last_frame_time": instance.last_frame_time,
        }

    def _run_pipeline_wrapper(self, instance: PipelineInstance) -> None:
        """Wrapper that runs the pipeline and handles crashes with auto-restart.

        This runs in a dedicated thread. Catches all exceptions from the pipeline
        factory to ensure one pipeline crash cannot affect others (isolation).
        """
        from logging_config import bind_machine_id

        machine_id = instance.machine_id
        # Bind machine_id to this thread's log context for structured logging
        bind_machine_id(machine_id)

        while not self._shutdown_event.is_set() and not instance.stop_event.is_set():
            try:
                with self._lock:
                    instance.status = PipelineStatus.RUNNING
                    instance.last_error = None

                # Run the actual pipeline (blocking call)
                self._pipeline_factory(
                    instance.machine_config,
                    instance.stop_event,
                    instance,
                )

                # If we get here cleanly, pipeline exited normally (stop was requested)
                with self._lock:
                    if instance.stop_event.is_set():
                        instance.status = PipelineStatus.STOPPED
                        break

            except Exception as exc:
                # Pipeline crashed — isolate the failure
                error_msg = f"{type(exc).__name__}: {exc}"
                logger.error(
                    "Pipeline for %s crashed: %s", machine_id, error_msg, exc_info=True
                )

                with self._lock:
                    instance.last_error = error_msg
                    instance.restart_count += 1

                    # Check if we've exceeded max restart attempts
                    if instance.restart_count > self._max_restart_attempts:
                        instance.status = PipelineStatus.FAILED
                        logger.error(
                            "Pipeline for %s marked FAILED after %d restart attempts",
                            machine_id, instance.restart_count - 1,
                        )
                        break

                    instance.status = PipelineStatus.ERROR

                # If stop was requested during crash handling, exit
                if instance.stop_event.is_set() or self._shutdown_event.is_set():
                    with self._lock:
                        instance.status = PipelineStatus.STOPPED
                    break

                # Wait before restarting (interruptible)
                logger.info(
                    "Pipeline for %s will restart in %.1fs (attempt %d/%d)",
                    machine_id, self._restart_delay,
                    instance.restart_count, self._max_restart_attempts,
                )
                if instance.stop_event.wait(timeout=self._restart_delay):
                    # Stop was requested during delay
                    with self._lock:
                        instance.status = PipelineStatus.STOPPED
                    break

                # Reset status to starting for the retry
                with self._lock:
                    instance.status = PipelineStatus.STARTING

        # Cleanup
        with self._lock:
            if instance.status not in (PipelineStatus.FAILED,):
                instance.status = PipelineStatus.STOPPED

    @staticmethod
    def _default_pipeline_factory(
        machine_config: Dict[str, Any],
        stop_event: threading.Event,
        instance: PipelineInstance,
    ) -> None:
        """Default pipeline factory — creates and runs CV pipeline components.

        This instantiates FrameCapture, PersonDetector, AntiCheatEngine,
        SessionManager, and LightDetector for the given machine, then runs
        the frame processing loop until stop_event is set.

        Detection parameters are read from instance.machine_config each frame
        to support hot-reload without pipeline restart (Requirement 24.1).
        """
        from cv_pipeline.capture import FrameCapture
        from cv_pipeline.detector import PersonDetector
        from cv_pipeline.light_detector import LightDetector
        from engine.anti_cheat import AntiCheatEngine
        from engine.session_manager import SessionManager
        from cv_pipeline.kalman_tracker import KalmanBoxTracker

        machine_id = machine_config["machine_id"]
        rtsp_url = machine_config.get("rtsp_url", "")

        logger.info("Starting CV pipeline components for machine %s", machine_id)

        # Create per-pipeline component instances
        capture = FrameCapture(rtsp_url=rtsp_url)
        detector = PersonDetector()
        anticheat = AntiCheatEngine()
        session_mgr = SessionManager()
        light_zone = machine_config.get("light_zone")
        light_detector = LightDetector(zone=light_zone) if light_zone else LightDetector()
        body_tracker = KalmanBoxTracker()

        # Store components on instance for external access
        instance.components = {
            "capture": capture,
            "detector": detector,
            "anticheat": anticheat,
            "session_manager": session_mgr,
            "light_detector": light_detector,
            "body_tracker": body_tracker,
        }

        # Start capture
        capture.start()

        try:
            # Wait for first frame
            while not stop_event.is_set():
                frame = capture.get_frame()
                if frame is not None:
                    break
                time.sleep(0.1)

            logger.info("CV pipeline running for machine %s", machine_id)

            last_processed_frame_id = -1

            # Main processing loop
            while not stop_event.is_set():
                frame = capture.get_frame()
                current_frame_id = capture.frame_count

                if frame is None or current_frame_id == last_processed_frame_id:
                    time.sleep(0.01)
                    continue

                last_processed_frame_id = current_frame_id
                instance.last_frame_time = time.time()

                # ── Read live config from instance (hot-reload support) ──
                # instance.machine_config may be updated by the API at any time
                # via update_pipeline_config(). Reading it each frame ensures
                # changes apply within one frame cycle (~5 seconds max).
                live_config = instance.machine_config

                # Apply dynamic confidence threshold if updated
                confidence_threshold = live_config.get(
                    "person_confidence_threshold",
                    live_config.get("confidence_threshold"),
                )
                if confidence_threshold is not None and hasattr(detector, "confidence_threshold"):
                    detector.confidence_threshold = confidence_threshold

                # Apply dynamic light zone if updated
                updated_light_zone = live_config.get("light_zone")
                if updated_light_zone is not None and hasattr(light_detector, "_zone"):
                    light_detector._zone = updated_light_zone

                # Person detection + Kalman smoothing
                raw_detected, raw_bbox = detector.detect(frame)
                body_detected, body_bbox = body_tracker.update(
                    raw_bbox if raw_detected else None
                )

                # Movement analysis (optical flow on body region)
                if body_detected and body_bbox:
                    bx1, by1, bx2, by2 = body_bbox
                    hf, wf = frame.shape[:2]
                    cx1 = max(0, bx1 + (bx2 - bx1) // 4)
                    cy1 = max(0, by1 + (by2 - by1) // 4)
                    cx2 = min(wf, bx2 - (bx2 - bx1) // 4)
                    cy2 = min(hf, by2 - (by2 - by1) // 4)
                    crop = frame[cy1:cy2, cx1:cx2] if cy2 > cy1 and cx2 > cx1 else None
                    movement_status = anticheat.check_movement(crop)
                else:
                    anticheat.reset()
                    movement_status = "NO_DATA"

                badge_static = movement_status == "ABANDONED"

                # Session state machine
                session_mgr.process_frame(
                    body_detected=body_detected,
                    badge_static=badge_static,
                )

        finally:
            # Ensure capture is released on exit
            try:
                capture.stop()
            except Exception:
                pass
            logger.info("CV pipeline stopped for machine %s", machine_id)
