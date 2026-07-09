"""Edge_Agent bootstrap — entrypoint and resilient startup sequence.

The Edge_Agent is the on-site process that owns the camera and runs the full CV
compute stack. This module is its entrypoint: it wires together the building
blocks (:class:`~edge.offline_queue.OfflineQueue`,
:class:`~edge.sync_client.SyncClient`,
:class:`~edge.local_camera_config.LocalCameraConfig`,
:class:`~engine.pipeline_orchestrator.PipelineOrchestrator`,
:class:`~edge.event_bridge.EventBridge`,
:class:`~edge.live_status.LiveStatusPublisher`, and
:class:`~edge.metadata_apply.MetadataApplier`) into a running service.

Startup sequence (Requirements 1.1, 1.3, 7.2, 12.5, 14.4):

1. **Load Local_Camera_Config** from the git-excluded local file. This *fails
   fast*: a missing or malformed mapping aborts startup before any pipeline is
   touched (Requirement 14.4).
2. **Construct the durable channel** — an :class:`OfflineQueue` (local SQLite)
   and a :class:`SyncClient` reading ``INGEST_API_KEY`` and
   ``CLOUD_SERVER_BASE_URL`` from the git-excluded ``config`` / ``.env``
   (Requirement 14.4).
3. **Pull Machine_Metadata** from the Cloud_Server (Requirement 7.2). If the
   pull fails because the cloud is unreachable, the last-known metadata (or an
   empty list on a cold start) is used instead and startup *continues* — the CV
   compute stack must keep running while the cloud is unreachable
   (Requirement 12.5). Startup is never aborted on a metadata pull failure.
4. **Build machine configs** by merging cloud metadata with local RTSP config
   (:meth:`LocalCameraConfig.build_machine_configs`); unmapped machines are
   skipped and warned (Requirement 7.8, handled by the loader).
5. **Start a CV pipeline** per config via the ``PipelineOrchestrator``. Each
   edge pipeline routes ``SessionManager`` events to the Sync_Client through an
   :class:`EventBridge` and emits Heartbeats / Snapshot_Thumbnails through a
   :class:`LiveStatusPublisher`.
6. **Launch the Sync_Client background loops** — the durable-event flusher and
   the Machine_Metadata poller. The poller's on-change callback feeds a
   :class:`MetadataApplier` so live config changes hot-reload or restart the
   affected pipeline.

Shutdown is graceful: the poller and flusher are stopped, all pipelines are
stopped, and the Offline_Queue is closed.

Requirements: 1.1, 1.3, 7.2, 12.5, 14.4
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import Any, Dict, List, Optional

import structlog

import config
from api.ingest_schemas import MachineMetadata
from edge.event_bridge import EventBridge
from edge.live_status import LiveStatusPublisher
from edge.local_camera_config import (
    DEFAULT_CONFIG_PATH,
    LocalCameraConfig,
    LocalCameraConfigError,
)
from edge.metadata_apply import MetadataApplier
from edge.offline_queue import OfflineQueue
from edge.sync_client import SyncClient, SyncClientError
from engine.pipeline_orchestrator import PipelineOrchestrator

logger = structlog.get_logger(__name__)

# Local, git-excludable path for the durable Offline_Queue SQLite file. Kept
# separate from the Cloud_Server database (which lives only on the cloud).
DEFAULT_QUEUE_DB_PATH = os.getenv("OFFLINE_QUEUE_DB_PATH", "edge_queue.db")


class EdgeAgentError(Exception):
    """Raised when the Edge_Agent cannot start or run."""


class EdgeAgent:
    """The on-site Edge_Agent service.

    All collaborators are injectable so the startup sequence can be exercised
    with fakes. When not injected they are constructed from the git-excluded
    ``config`` / ``.env`` values (Requirement 14.4).

    Parameters
    ----------
    camera_config_path:
        Path to the git-excluded Local_Camera_Config mapping file.
    queue:
        A durable :class:`OfflineQueue`. Constructed at ``DEFAULT_QUEUE_DB_PATH``
        when omitted.
    sync_client:
        The :class:`SyncClient`. Built via :meth:`SyncClient.from_config` when
        omitted (reads ``CLOUD_SERVER_BASE_URL`` / ``INGEST_API_KEY``).
    orchestrator:
        The :class:`PipelineOrchestrator`. Constructed with the edge pipeline
        factory when omitted.
    camera_config:
        A pre-loaded :class:`LocalCameraConfig` (skips the file load — mainly
        for tests).
    poll_interval_s:
        Metadata poll interval; defaults to ``config.METADATA_POLL_INTERVAL_SECONDS``.
    """

    def __init__(
        self,
        *,
        camera_config_path: str = DEFAULT_CONFIG_PATH,
        queue: Optional[OfflineQueue] = None,
        sync_client: Optional[SyncClient] = None,
        orchestrator: Optional[PipelineOrchestrator] = None,
        camera_config: Optional[LocalCameraConfig] = None,
        poll_interval_s: Optional[float] = None,
    ) -> None:
        self._camera_config_path = camera_config_path
        self._queue = queue
        self._sync = sync_client
        self._orchestrator = orchestrator
        self._camera_config = camera_config
        self._poll_interval_s = poll_interval_s

        self._applier: Optional[MetadataApplier] = None
        # machine_id -> last-applied MachineMetadata, so the poller's on-change
        # callback can diff and decide hot-reload vs restart per machine.
        self._metadata_by_id: Dict[str, MachineMetadata] = {}
        # The event loop the agent runs on; captured at startup so the
        # thread-based pipelines can schedule best-effort live-status sends.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._started = False
        self._stop_event: Optional[asyncio.Event] = None

    # ── Public lifecycle ──────────────────────────────────
    async def startup(self) -> None:
        """Run the full startup sequence (Requirements 1.1, 1.3, 7.2, 12.5, 14.4)."""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()

        # 1. Local_Camera_Config — fail fast on missing/malformed (Req 14.4).
        if self._camera_config is None:
            try:
                self._camera_config = LocalCameraConfig.load(self._camera_config_path)
            except LocalCameraConfigError as exc:
                logger.error(
                    "Edge_Agent startup aborted: Local_Camera_Config invalid",
                    path=self._camera_config_path,
                    error=str(exc),
                )
                raise
        logger.info(
            "Edge_Agent starting",
            camera_config_path=self._camera_config_path,
            mapped_machines=self._camera_config.machine_ids(),
        )

        # 2. Durable channel: Offline_Queue + Sync_Client (Req 14.4).
        if self._queue is None:
            self._queue = OfflineQueue(DEFAULT_QUEUE_DB_PATH)
        if self._sync is None:
            try:
                self._sync = SyncClient.from_config(self._queue)
            except SyncClientError as exc:
                # A misconfigured base URL / missing key is a startup error:
                # the durable channel cannot be constructed at all.
                logger.error(
                    "Edge_Agent startup aborted: Sync_Client misconfigured",
                    error=str(exc),
                )
                raise EdgeAgentError(f"Sync_Client construction failed: {exc}") from exc

        # 3. Orchestrator wired with the edge pipeline factory.
        if self._orchestrator is None:
            self._orchestrator = PipelineOrchestrator(
                pipeline_factory=self._make_edge_pipeline_factory()
            )
        self._applier = MetadataApplier(self._orchestrator)

        # 4. Pull Machine_Metadata; retain last-known / empty on failure so the
        #    CV stack still starts while the cloud is unreachable (Req 7.2, 12.5).
        metadata = await self._pull_metadata_resilient()
        self._metadata_by_id = {m.machine_id: m for m in metadata}

        # 5. Build machine configs and start a pipeline per config (Req 1.1).
        started = self._start_pipelines(metadata)

        # 6. Launch the Sync_Client background loops (flusher + poller).
        self._sync.start_flusher()
        self._sync.start_metadata_poller(
            on_change=self._on_metadata_change,
            interval_s=self._poll_interval_s,
        )

        self._started = True
        logger.info(
            "Edge_Agent started",
            pipelines_started=started,
            metadata_machines=len(metadata),
            cloud_reachable=self._sync.is_reachable,
        )

    async def run(self) -> None:
        """Start the agent and run until a shutdown signal is received."""
        self._stop_event = asyncio.Event()
        self._install_signal_handlers()
        await self.startup()
        logger.info("Edge_Agent running — awaiting shutdown signal")
        try:
            await self._stop_event.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully stop the background loops, pipelines, and durable queue."""
        if not self._started:
            return
        logger.info("Edge_Agent shutting down")
        # Stop background loops first so nothing new is polled/flushed.
        if self._sync is not None:
            try:
                await self._sync.stop_metadata_poller()
            except Exception as exc:  # noqa: BLE001 - never fail shutdown
                logger.warning("Error stopping metadata poller", error=str(exc))
            try:
                await self._sync.stop_flusher()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping flusher", error=str(exc))

        # Stop the CV compute stack.
        if self._orchestrator is not None:
            try:
                self._orchestrator.stop_all(timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error stopping pipelines", error=str(exc))

        # Close the durable queue (flush WAL, release the file).
        if self._queue is not None:
            try:
                self._queue.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error closing Offline_Queue", error=str(exc))

        self._started = False
        logger.info("Edge_Agent shutdown complete")

    # ── Startup helpers ───────────────────────────────────
    async def _pull_metadata_resilient(self) -> List[MachineMetadata]:
        """Pull Machine_Metadata, keeping last-known / empty on failure.

        Requirement 7.2 pulls metadata on startup; Requirement 12.5 requires the
        CV compute stack to keep running while the cloud is unreachable — so a
        failed pull must NOT abort startup. On failure the last-known metadata is
        retained (Requirement 7.9); on a cold start with nothing known, an empty
        list is used and pipelines simply wait for the poller to supply metadata.
        """
        try:
            metadata = await self._sync.pull_metadata()
            logger.info("Machine_Metadata pulled on startup", count=len(metadata))
            return metadata
        except SyncClientError as exc:
            last_known = self._sync.last_known_metadata or []
            logger.warning(
                "Machine_Metadata pull failed on startup — continuing with "
                "last-known metadata (CV stack keeps running)",
                error=str(exc),
                last_known_count=len(last_known),
            )
            return list(last_known)

    def _start_pipelines(self, metadata: List[MachineMetadata]) -> int:
        """Build machine configs and start a pipeline for each (Requirement 1.1)."""
        assert self._camera_config is not None and self._orchestrator is not None
        configs = self._camera_config.build_machine_configs(metadata)
        started = 0
        for cfg in configs:
            if self._orchestrator.start_pipeline(cfg):
                started += 1
            else:
                logger.error(
                    "Failed to start pipeline", machine_id=cfg.get("machine_id")
                )
        logger.info(
            "Edge_Agent pipeline startup",
            started=started,
            configs=len(configs),
        )
        return started

    def _start_pipeline_for(self, meta: MachineMetadata) -> None:
        """Start a single pipeline for newly-appeared metadata (if locally mapped)."""
        assert self._camera_config is not None and self._orchestrator is not None
        configs = self._camera_config.build_machine_configs([meta])
        for cfg in configs:
            if self._orchestrator.start_pipeline(cfg):
                logger.info(
                    "Started pipeline for newly-added machine",
                    machine_id=cfg.get("machine_id"),
                )

    # ── Metadata poller callback (Requirement 7.4 via MetadataApplier) ──
    def _on_metadata_change(self, metadata: List[MachineMetadata]) -> None:
        """Apply polled Machine_Metadata changes to running pipelines.

        Invoked by the Sync_Client metadata poller whenever the polled metadata
        differs from the last-known value. For each machine: a brand-new machine
        starts a pipeline; a changed machine is reconciled via the
        :class:`MetadataApplier` (hot-reload vs restart per Requirement 7.4).
        """
        if self._applier is None:
            return
        new_by_id = {m.machine_id: m for m in metadata}
        for machine_id, new_meta in new_by_id.items():
            old_meta = self._metadata_by_id.get(machine_id)
            if old_meta is None:
                # New machine appeared in cloud metadata — start it if mapped.
                self._start_pipeline_for(new_meta)
                continue
            try:
                result = self._applier.apply_change(old_meta, new_meta)
                if result.action != "none":
                    logger.info(
                        "Applied Machine_Metadata change",
                        machine_id=machine_id,
                        action=result.action,
                        changed_keys=sorted(result.changed_keys),
                    )
            except Exception as exc:  # noqa: BLE001 - keep the poller alive
                logger.warning(
                    "Failed to apply metadata change",
                    machine_id=machine_id,
                    error=str(exc),
                )
        self._metadata_by_id = new_by_id

    # ── Edge pipeline factory ─────────────────────────────
    def _make_edge_pipeline_factory(self):
        """Build the pipeline factory the orchestrator runs per machine.

        The returned callable matches the orchestrator's factory contract
        ``(machine_config, stop_event, instance)`` and runs the edge CV loop,
        routing durable events to the Sync_Client and emitting best-effort
        Heartbeats / Snapshot_Thumbnails.
        """
        sync_client = self._sync
        agent = self

        def factory(machine_config, stop_event, instance):
            _run_edge_pipeline(
                sync_client, agent._loop, machine_config, stop_event, instance
            )

        return factory

    # ── Signals ───────────────────────────────────────────
    def _install_signal_handlers(self) -> None:
        """Wire SIGINT/SIGTERM to trigger a graceful shutdown.

        Uses the loop's native signal handling where available (POSIX) and
        falls back to :func:`signal.signal` on platforms (e.g. Windows) where
        ``add_signal_handler`` is not implemented.
        """
        loop = asyncio.get_running_loop()

        def _trigger() -> None:
            logger.info("Edge_Agent received shutdown signal")
            if self._stop_event is not None:
                self._stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _trigger)
            except (NotImplementedError, RuntimeError, ValueError):
                # Windows / non-main-thread: fall back to signal.signal, which
                # must hand control back to the loop thread-safely.
                try:
                    signal.signal(
                        sig,
                        lambda *_: loop.call_soon_threadsafe(_trigger),
                    )
                except (ValueError, OSError):  # pragma: no cover - defensive
                    pass


def _run_edge_pipeline(sync_client, loop, machine_config, stop_event, instance) -> None:
    """Run the edge CV loop for one machine (executed in an orchestrator thread).

    Mirrors the orchestrator's default CV loop but routes ``SessionManager``
    events to the Cloud_Server via an :class:`EventBridge` (durable, synchronous
    enqueue) and emits Heartbeats / Snapshot_Thumbnails via a
    :class:`LiveStatusPublisher` (best-effort, scheduled onto the agent's event
    loop). CV imports are deferred so :mod:`edge.agent` stays importable on hosts
    without the CV stack installed.
    """
    from cv_pipeline.capture import FrameCapture
    from cv_pipeline.detector import PersonDetector
    from cv_pipeline.light_detector import LightDetector
    from cv_pipeline.kalman_tracker import KalmanBoxTracker
    from engine.anti_cheat import AntiCheatEngine
    from engine.session_manager import SessionManager

    machine_id = machine_config["machine_id"]
    rtsp_url = machine_config.get("rtsp_url", "")

    logger.info("Starting edge CV pipeline", machine_id=machine_id)

    capture = FrameCapture(rtsp_url=rtsp_url)
    detector = PersonDetector()
    anticheat = AntiCheatEngine()
    session_mgr = SessionManager(machine_id=machine_id)
    light_zone = machine_config.get("light_zone")
    light_detector = LightDetector(zone=light_zone) if light_zone else LightDetector()
    body_tracker = KalmanBoxTracker()

    # Edge-specific adapters. The bridge forwards durable events (sync); the
    # publisher produces best-effort live status (async).
    bridge = EventBridge(
        sync_client, machine_id=machine_id, jpeg_quality=config.EDGE_JPEG_QUALITY
    )
    publisher = LiveStatusPublisher(
        sync_client,
        machine_id=machine_id,
        heartbeat_interval_s=config.EDGE_HEARTBEAT_INTERVAL_SECONDS,
        heartbeat_tolerance_s=config.EDGE_HEARTBEAT_TOLERANCE_SECONDS,
        snapshot_interval_s=config.EDGE_SNAPSHOT_INTERVAL_SECONDS,
        thumbnail_max_dim=config.EDGE_SNAPSHOT_MAX_DIMENSION,
        healthy_max_age_s=config.EDGE_CAMERA_HEALTHY_MAX_AGE_SECONDS,
        degraded_max_age_s=config.EDGE_CAMERA_DEGRADED_MAX_AGE_SECONDS,
        jpeg_quality=config.EDGE_JPEG_QUALITY,
    )

    instance.components = {
        "capture": capture,
        "detector": detector,
        "anticheat": anticheat,
        "session_manager": session_mgr,
        "light_detector": light_detector,
        "body_tracker": body_tracker,
        "event_bridge": bridge,
        "live_status": publisher,
    }

    capture.start()
    try:
        # Wait for the first frame.
        while not stop_event.is_set():
            if capture.get_frame() is not None:
                break
            time.sleep(0.1)

        logger.info("Edge CV pipeline running", machine_id=machine_id)
        last_processed_frame_id = -1
        last_frame_arrival = time.monotonic()

        while not stop_event.is_set():
            frame = capture.get_frame()
            current_frame_id = capture.frame_count
            if frame is None or current_frame_id == last_processed_frame_id:
                time.sleep(0.01)
                continue

            last_processed_frame_id = current_frame_id
            now = time.monotonic()
            frame_age = now - last_frame_arrival
            last_frame_arrival = now
            instance.last_frame_time = time.time()

            # Hot-reloadable detection params read live each frame.
            live_config = instance.machine_config
            confidence_threshold = live_config.get(
                "person_confidence_threshold",
                live_config.get("confidence_threshold"),
            )
            if confidence_threshold is not None and hasattr(
                detector, "confidence_threshold"
            ):
                detector.confidence_threshold = confidence_threshold
            updated_light_zone = live_config.get("light_zone")
            if updated_light_zone is not None and hasattr(light_detector, "_zone"):
                light_detector._zone = updated_light_zone

            # Person detection + Kalman smoothing.
            raw_detected, raw_bbox = detector.detect(frame)
            body_detected, body_bbox = body_tracker.update(
                raw_bbox if raw_detected else None
            )

            # Movement analysis on the body crop.
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

            # Session state machine + light detection.
            snapshot = session_mgr.process_frame(
                body_detected=body_detected, badge_static=badge_static
            )
            light_result = light_detector.detect(frame)

            # Route durable events to the Cloud_Server (synchronous enqueue).
            try:
                bridge.process(snapshot, frame=frame, light_result=light_result)
            except Exception as exc:  # noqa: BLE001 - never kill the CV loop
                logger.warning(
                    "EventBridge failed to route events",
                    machine_id=machine_id,
                    error=str(exc),
                )

            # Best-effort live status — schedule onto the agent's event loop.
            if loop is not None and not loop.is_closed():
                machine_light = light_result.get("status", "UNKNOWN")
                coro = publisher.publish(
                    snapshot,
                    frame=frame,
                    last_frame_age_s=frame_age,
                    connected=True,
                    machine_light=machine_light,
                )
                try:
                    asyncio.run_coroutine_threadsafe(coro, loop)
                except RuntimeError:  # pragma: no cover - loop stopping
                    coro.close()
    finally:
        try:
            capture.stop()
        except Exception:  # noqa: BLE001
            pass
        logger.info("Edge CV pipeline stopped", machine_id=machine_id)


def main() -> None:
    """Edge_Agent entrypoint. Configure logging and run until shutdown."""
    try:
        from logging_config import setup_logging

        setup_logging(log_level="INFO", file_path="edge_agent.log")
    except Exception:  # noqa: BLE001 - logging is best-effort at boot
        pass

    agent = EdgeAgent()
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:  # pragma: no cover - interactive interrupt
        logger.info("Edge_Agent interrupted")


if __name__ == "__main__":
    main()
