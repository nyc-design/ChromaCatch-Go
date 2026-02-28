"""Manages connected WebSocket client sessions."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import numpy as np
from fastapi import WebSocket

from shared.messages import ClientStatus, HIDCommandMessage

logger = logging.getLogger(__name__)


@dataclass
class ClientSession:
    """Represents a connected local client."""

    websocket: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_frame_at: float = 0.0
    last_status: ClientStatus | None = None
    frames_received: int = 0
    latest_frame: np.ndarray | None = None


class SessionManager:
    """Manages client WebSocket connections.

    Supports multiple clients (e.g., multiple hunting stations).
    For MVP, typically just one client is connected.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, client_id: str, websocket: WebSocket) -> ClientSession:
        async with self._lock:
            session = ClientSession(websocket=websocket)
            self._sessions[client_id] = session
            logger.info("Client registered: %s", client_id)
            return session

    async def unregister(self, client_id: str) -> None:
        async with self._lock:
            if client_id in self._sessions:
                del self._sessions[client_id]
                logger.info("Client unregistered: %s", client_id)

    def get_session(self, client_id: str) -> ClientSession | None:
        return self._sessions.get(client_id)

    async def send_command(self, client_id: str, command: HIDCommandMessage) -> None:
        """Send a HID command to a specific client."""
        session = self._sessions.get(client_id)
        if session is None:
            raise ValueError(f"No client connected with id: {client_id}")
        await session.websocket.send_text(command.model_dump_json())

    async def broadcast_command(self, command: HIDCommandMessage) -> None:
        """Send a command to all connected clients."""
        for client_id, session in self._sessions.items():
            try:
                await session.websocket.send_text(command.model_dump_json())
            except Exception as e:
                logger.error("Failed to send to client %s: %s", client_id, e)

    @property
    def connected_clients(self) -> list[str]:
        return list(self._sessions.keys())

    def get_latest_frame(self, client_id: str) -> np.ndarray | None:
        session = self._sessions.get(client_id)
        if session:
            return session.latest_frame
        return None
