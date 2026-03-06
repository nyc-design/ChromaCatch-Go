"""Manages connected location WebSocket client sessions."""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

from shared.messages import LocationUpdateMessage

logger = logging.getLogger(__name__)


@dataclass
class LocationClientSession:
    """Represents a connected iOS app client for location updates."""

    websocket: WebSocket
    connected_at: float = field(default_factory=time.time)
    last_location: LocationUpdateMessage | None = None


class LocationSessionManager:
    """Manages WebSocket connections from iOS apps receiving location updates."""

    def __init__(self) -> None:
        self._sessions: dict[str, LocationClientSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, client_id: str, websocket: WebSocket) -> LocationClientSession:
        async with self._lock:
            session = LocationClientSession(websocket=websocket)
            self._sessions[client_id] = session
            logger.info("Location client registered: %s", client_id)
            return session

    async def unregister(self, client_id: str) -> None:
        async with self._lock:
            if client_id in self._sessions:
                del self._sessions[client_id]
                logger.info("Location client unregistered: %s", client_id)

    def get_session(self, client_id: str) -> LocationClientSession | None:
        return self._sessions.get(client_id)

    async def send_location(self, client_id: str, message: LocationUpdateMessage) -> None:
        """Send a location update to a specific client."""
        session = self._sessions.get(client_id)
        if session is None:
            raise ValueError(f"No location client connected with id: {client_id}")
        await session.websocket.send_text(message.model_dump_json())
        session.last_location = message

    async def broadcast_location(self, message: LocationUpdateMessage) -> int:
        """Send a location update to all connected clients. Returns count sent."""
        sent = 0
        for client_id in list(self._sessions.keys()):
            try:
                await self.send_location(client_id, message)
                sent += 1
            except Exception as e:
                logger.error("Failed to send location to %s: %s", client_id, e)
        return sent

    @property
    def connected_clients(self) -> list[str]:
        return list(self._sessions.keys())
