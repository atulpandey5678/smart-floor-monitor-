"""WebSocket handler and broadcaster for real-time multi-machine updates.

Supports per-client subscriptions (specific machine IDs or all machines),
initial state snapshots on connect, per-machine targeted broadcasts,
message envelope format, and reconnection reconciliation.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
"""

import asyncio
import json
import structlog
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = structlog.get_logger(__name__)

# Maximum concurrent WebSocket connections (Requirement 4.6)
MAX_CONNECTIONS = 50

# Auto-subscribe delay: if client doesn't send subscribe within this many seconds,
# auto-subscribe to all machines for backward compatibility
_AUTO_SUBSCRIBE_DELAY = 2.0


class WebSocketManager:
    """Manages multi-machine WebSocket connections with per-client subscriptions.

    Tracks connected clients, their subscriptions, handles connect/disconnect,
    and provides targeted broadcast methods for per-machine state updates.
    Limits concurrent connections to MAX_CONNECTIONS.

    Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6
    """

    def __init__(self):
        self._clients: Set[WebSocket] = set()
        # Maps each client to their subscribed machine IDs ("*" means all)
        self._subscriptions: Dict[WebSocket, Set[str]] = {}
        self._lock = asyncio.Lock()
        # Track whether a client has explicitly subscribed
        self._has_subscribed: Dict[WebSocket, bool] = {}

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> bool:
        """Accept a new WebSocket connection.

        Returns False if the connection limit is reached.
        """
        if len(self._clients) >= MAX_CONNECTIONS:
            await websocket.close(code=1013, reason="Max connections reached")
            logger.warning(f"Connection rejected: max {MAX_CONNECTIONS} clients reached")
            return False

        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
            self._subscriptions[websocket] = set()
            self._has_subscribed[websocket] = False
        logger.info(f"WebSocket connected. Clients: {self.client_count}")
        return True

    async def disconnect(self, websocket: WebSocket):
        """Remove a disconnected client and clean up subscriptions."""
        async with self._lock:
            self._clients.discard(websocket)
            self._subscriptions.pop(websocket, None)
            self._has_subscribed.pop(websocket, None)
        logger.info(f"WebSocket disconnected. Clients: {self.client_count}")

    def subscribe(self, websocket: WebSocket, machine_ids: Set[str]):
        """Add machine subscriptions for a client.

        Use {"*"} to subscribe to all machines.
        """
        if websocket in self._subscriptions:
            self._subscriptions[websocket].update(machine_ids)
            self._has_subscribed[websocket] = True

    def unsubscribe(self, websocket: WebSocket, machine_ids: Set[str]):
        """Remove machine subscriptions for a client."""
        if websocket in self._subscriptions:
            self._subscriptions[websocket] -= machine_ids

    def get_subscriptions(self, websocket: WebSocket) -> Set[str]:
        """Get current subscriptions for a client."""
        return self._subscriptions.get(websocket, set())

    def is_subscribed(self, websocket: WebSocket, machine_id: str) -> bool:
        """Check if a client is subscribed to a specific machine's updates."""
        subs = self._subscriptions.get(websocket, set())
        return "*" in subs or machine_id in subs

    async def send_to_client(self, websocket: WebSocket, data: dict) -> bool:
        """Send data to a specific client. Returns False if send fails."""
        try:
            message = json.dumps(data, default=str)
            await websocket.send_text(message)
            return True
        except Exception:
            return False

    async def broadcast(self, data: dict):
        """Send data to all connected clients (backward-compatible).

        Removes any client that fails to receive the message.
        """
        if not self._clients:
            return

        message = json.dumps(data, default=str)
        disconnected = []

        for client in list(self._clients):
            try:
                await client.send_text(message)
            except Exception:
                disconnected.append(client)

        # Clean up failed clients
        if disconnected:
            async with self._lock:
                for client in disconnected:
                    self._clients.discard(client)
                    self._subscriptions.pop(client, None)
                    self._has_subscribed.pop(client, None)
            logger.info(f"Removed {len(disconnected)} disconnected clients")

    async def broadcast_machine_state(self, machine_id: str, payload: dict):
        """Broadcast state update for a specific machine to subscribed clients.

        Uses message envelope format (Requirement 4.4):
        {type: "state_update", machine_id: "...", timestamp: "...", payload: {...}}
        """
        envelope = {
            "type": "state_update",
            "machine_id": machine_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }

        disconnected = []

        for client in list(self._clients):
            if self.is_subscribed(client, machine_id):
                success = await self.send_to_client(client, envelope)
                if not success:
                    disconnected.append(client)

        if disconnected:
            async with self._lock:
                for client in disconnected:
                    self._clients.discard(client)
                    self._subscriptions.pop(client, None)
                    self._has_subscribed.pop(client, None)

    async def send_snapshot(self, websocket: WebSocket, machines_state: dict):
        """Send initial state snapshot to a client (Requirement 4.2).

        Message format:
        {type: "snapshot", timestamp: "...", payload: {machines: {...}}}
        """
        envelope = {
            "type": "snapshot",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {"machines": machines_state},
        }
        await self.send_to_client(websocket, envelope)

    async def send_reconciliation(self, websocket: WebSocket, machines_state: dict):
        """Send full state reconciliation on reconnect (Requirement 4.5).

        Same format as snapshot but indicates it's a reconciliation for subscribed machines.
        """
        # Filter to only subscribed machines
        subs = self.get_subscriptions(websocket)
        if "*" in subs:
            filtered_state = machines_state
        else:
            filtered_state = {
                mid: state for mid, state in machines_state.items()
                if mid in subs
            }

        envelope = {
            "type": "snapshot",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {"machines": filtered_state},
        }
        await self.send_to_client(websocket, envelope)

    async def auto_subscribe_if_needed(self, websocket: WebSocket):
        """Auto-subscribe a client to all machines if they haven't subscribed yet.

        Called after _AUTO_SUBSCRIBE_DELAY seconds for backward compatibility.
        """
        if websocket in self._has_subscribed and not self._has_subscribed[websocket]:
            self.subscribe(websocket, {"*"})
            logger.info("Auto-subscribed client to all machines (backward compat)")


# Global manager instance
ws_manager = WebSocketManager()

# ── State Providers ──────────────────────────────────────────────────

# Legacy single-machine state provider (backward compat)
_get_state_fn = None

# Multi-machine orchestrator reference
_orchestrator = None


def set_state_provider(fn):
    """Set the function that returns current state for broadcasting (legacy)."""
    global _get_state_fn
    _get_state_fn = fn


def set_orchestrator(orchestrator):
    """Set the PipelineOrchestrator for multi-machine state access.

    The orchestrator provides:
      - get_all_statuses() -> Dict[str, Dict] (pipeline status per machine)
      - get_pipeline_instance(machine_id) -> PipelineInstance (for component access)
    """
    global _orchestrator
    _orchestrator = orchestrator


def _get_all_machines_state() -> dict:
    """Collect current state from all active machines.

    Returns dict mapping machine_id to state dict.
    """
    if _orchestrator is not None:
        statuses = _orchestrator.get_all_statuses()
        machines_state = {}
        for machine_id, status_info in statuses.items():
            instance = _orchestrator.get_pipeline_instance(machine_id)
            machine_state = {
                "machine_id": machine_id,
                "pipeline_status": status_info.get("status", "unknown"),
                "last_error": status_info.get("last_error"),
                "last_frame_time": status_info.get("last_frame_time", 0.0),
            }

            # Try to get session state from the SessionManager component
            if instance and instance.components:
                session_mgr = instance.components.get("session_manager")
                if session_mgr and hasattr(session_mgr, "get_state"):
                    session_state = session_mgr.get_state()
                    machine_state.update(session_state)
                elif session_mgr and hasattr(session_mgr, "_state"):
                    machine_state["state"] = str(session_mgr._state)

            machines_state[machine_id] = machine_state
        return machines_state

    # Fallback: legacy single-machine state
    if _get_state_fn:
        state = _get_state_fn()
        machine_id = state.get("machine_id", "M-01")
        return {machine_id: state}

    return {}


# ── WebSocket Endpoint ───────────────────────────────────────────────

async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint at /ws — accepts connection, handles subscriptions.

    Protocol:
      Client -> Server:
        {"type": "subscribe", "machine_ids": ["M-01", "M-02"]}  (or ["*"] for all)
        {"type": "unsubscribe", "machine_ids": ["M-01"]}

      Server -> Client:
        {"type": "snapshot", "timestamp": "...", "payload": {"machines": {...}}}
        {"type": "state_update", "machine_id": "...", "timestamp": "...", "payload": {...}}
        {"type": "subscribe_ack", "machine_ids": [...]}
        {"type": "error", "message": "..."}
    """
    connected = await ws_manager.connect(websocket)
    if not connected:
        return

    # Send initial snapshot with all active machines (Requirement 4.2)
    try:
        machines_state = _get_all_machines_state()
        await ws_manager.send_snapshot(websocket, machines_state)
    except Exception as e:
        logger.error(f"Failed to send initial snapshot: {e}")

    # Schedule auto-subscribe for backward compatibility
    auto_sub_task = asyncio.create_task(_auto_subscribe_after_delay(websocket))

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                await _handle_client_message(websocket, raw)
            except asyncio.TimeoutError:
                # No message — connection still alive, continue
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        auto_sub_task.cancel()
        await ws_manager.disconnect(websocket)


async def _auto_subscribe_after_delay(websocket: WebSocket):
    """Wait for auto-subscribe delay, then subscribe client to all if needed."""
    try:
        await asyncio.sleep(_AUTO_SUBSCRIBE_DELAY)
        await ws_manager.auto_subscribe_if_needed(websocket)
    except asyncio.CancelledError:
        pass


async def _handle_client_message(websocket: WebSocket, raw: str):
    """Process a client-to-server WebSocket message."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await ws_manager.send_to_client(websocket, {
            "type": "error",
            "message": "Invalid JSON",
        })
        return

    msg_type = msg.get("type")

    if msg_type == "subscribe":
        machine_ids = msg.get("machine_ids", [])
        if not isinstance(machine_ids, list) or not machine_ids:
            await ws_manager.send_to_client(websocket, {
                "type": "error",
                "message": "machine_ids must be a non-empty list",
            })
            return

        ws_manager.subscribe(websocket, set(machine_ids))

        # Send acknowledgment
        await ws_manager.send_to_client(websocket, {
            "type": "subscribe_ack",
            "machine_ids": machine_ids,
        })

        # Send reconciliation with current state for subscribed machines (Requirement 4.5)
        machines_state = _get_all_machines_state()
        await ws_manager.send_reconciliation(websocket, machines_state)

        logger.debug(f"Client subscribed to machines: {machine_ids}")

    elif msg_type == "unsubscribe":
        machine_ids = msg.get("machine_ids", [])
        if not isinstance(machine_ids, list) or not machine_ids:
            await ws_manager.send_to_client(websocket, {
                "type": "error",
                "message": "machine_ids must be a non-empty list",
            })
            return

        ws_manager.unsubscribe(websocket, set(machine_ids))
        logger.debug(f"Client unsubscribed from machines: {machine_ids}")

    elif msg_type == "request_snapshot":
        # Client requests a full state reconciliation (e.g., after reconnect)
        # Requirement 4.5: Send full state reconciliation on reconnect
        machines_state = _get_all_machines_state()
        await ws_manager.send_reconciliation(websocket, machines_state)
        logger.debug("Sent state reconciliation on client request")

    else:
        await ws_manager.send_to_client(websocket, {
            "type": "error",
            "message": f"Unknown message type: {msg_type}",
        })


# ── Broadcast Loop ───────────────────────────────────────────────────

async def broadcast_loop():
    """Background task that broadcasts per-machine state every 1 second.

    Iterates all active pipelines and sends targeted updates to subscribed clients.
    Call this as an asyncio task from the main application startup.

    Requirements: 4.3, 4.4
    """
    while True:
        try:
            if ws_manager.client_count > 0:
                machines_state = _get_all_machines_state()

                # Broadcast each machine's state to subscribed clients
                for machine_id, state in machines_state.items():
                    await ws_manager.broadcast_machine_state(machine_id, state)

        except Exception as e:
            logger.error(f"Broadcast error: {e}")
        await asyncio.sleep(1)
