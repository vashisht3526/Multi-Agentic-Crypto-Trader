from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

LOGGER = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts real-time updates."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.append(websocket)
        LOGGER.info("WebSocket client connected. Total: %d", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            if websocket in self._connections:
                self._connections.remove(websocket)
        LOGGER.info("WebSocket client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, default=str)
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._connections:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._connections.remove(ws)

    @property
    def client_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()
