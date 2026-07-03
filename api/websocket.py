"""WebSocket handler and broadcaster for real-time updates."""

import asyncio
import json
import logging
from typing import List

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Maximum concurrent WebSocket connections
MAX_CONNECTIONS = 10


class WebSocketManager:
    """Manages WebSocket connections and broadcasts state updates.

    Tracks connected clients, handles connect/disconnect gracefully,
    and provides a broadcast method to send state to all clients.
    Limits concurrent connections to MAX_CONNECTIONS.
    """

    def __init__(self):
        self._clients: List[WebSocket] = []
        self._lock = asyncio.Lock()

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
            self._clients.append(websocket)
        logger.info(f"WebSocket connected. Clients: {self.client_count}")
        return True

    async def disconnect(self, websocket: WebSocket):
        """Remove a disconnected client."""
        async with self._lock:
            if websocket in self._clients:
                self._clients.remove(websocket)
        logger.info(f"WebSocket disconnected. Clients: {self.client_count}")

    async def broadcast(self, data: dict):
        """Send data to all connected clients.

        Removes any client that fails to receive the message.
        """
        if not self._clients:
            return

        message = json.dumps(data, default=str)
        disconnected = []

        for client in self._clients[:]:  # iterate copy
            try:
                await client.send_text(message)
            except Exception:
                disconnected.append(client)

        # Clean up failed clients
        if disconnected:
            async with self._lock:
                for client in disconnected:
                    if client in self._clients:
                        self._clients.remove(client)
            logger.info(f"Removed {len(disconnected)} disconnected clients")


# Global manager instance
ws_manager = WebSocketManager()

# Shared state reference (set from main.py)
_get_state_fn = None


def set_state_provider(fn):
    """Set the function that returns current state for broadcasting."""
    global _get_state_fn
    _get_state_fn = fn


async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint at /ws — accepts connection and keeps alive."""
    connected = await ws_manager.connect(websocket)
    if not connected:
        return

    try:
        while True:
            # Keep connection alive; we don't expect client messages
            # but need to detect disconnection
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # No message received — that's fine, connection still alive
                pass
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)


async def broadcast_loop():
    """Background task that broadcasts state every 1 second.

    Call this as an asyncio task from the main application startup.
    """
    while True:
        try:
            if _get_state_fn and ws_manager.client_count > 0:
                state = _get_state_fn()
                await ws_manager.broadcast(state)
        except Exception as e:
            logger.error(f"Broadcast error: {e}")
        await asyncio.sleep(1)
