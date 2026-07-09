"""Edge_Agent package.

On-site components that own the camera and push results to the Cloud_Server:
the durable :class:`~edge.offline_queue.OfflineQueue`, the Local_Camera_Config
loader, the Sync_Client, and the edge bootstrap. These modules run only on the
Edge_Agent and never on the Cloud_Server.
"""

from edge.event_bridge import EventBridge
from edge.live_status import LiveStatusPublisher, classify_camera_health
from edge.local_camera_config import (
    CameraEntry,
    LocalCameraConfig,
    LocalCameraConfigError,
)
from edge.metadata_apply import (
    HOT_RELOADABLE_KEYS,
    MetadataApplier,
    MetadataApplyResult,
    diff_metadata,
)
from edge.offline_queue import OfflineQueue, OutboundEvent

__all__ = [
    "OfflineQueue",
    "OutboundEvent",
    "LocalCameraConfig",
    "LocalCameraConfigError",
    "CameraEntry",
    "EventBridge",
    "LiveStatusPublisher",
    "classify_camera_health",
    "MetadataApplier",
    "MetadataApplyResult",
    "diff_metadata",
    "HOT_RELOADABLE_KEYS",
]
