"""Manages connected WebSocket client sessions."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

import numpy as np
from fastapi import WebSocket

from shared.messages import ClientStatus, CommandAck, HIDCommandMessage

logger = logging.getLogger(__name__)


ChannelType = Literal["frame", "control"]


@dataclass
class ClientSession:
    """Represents a connected local client."""

    frame_websocket: WebSocket | None = None
    control_websocket: WebSocket | None = None
    connected_at: float = field(default_factory=time.time)
    last_frame_at: float = 0.0
    last_status: ClientStatus | None = None
    frames_received: int = 0
    latest_frame: np.ndarray | None = None
    latest_frame_jpeg: bytes | None = None
    latest_frame_sequence: int = 0
    last_audio_at: float = 0.0
    audio_chunks_received: int = 0
    latest_audio_chunk: bytes | None = None
    latest_audio_sequence: int = 0
    latest_audio_sample_rate: int = 0
    latest_audio_channels: int = 0
    latest_audio_format: str = "s16le"
    next_command_sequence: int = 0
    pending_commands: dict[str, float] = field(default_factory=dict)
    commands_sent: int = 0
    commands_acked: int = 0
    last_command_rtt_ms: float | None = None
    last_frame_latency_ms: float | None = None

    @property
    def websocket(self) -> WebSocket | None:
        """Backward-compatible alias for the primary transport."""
        return self.frame_websocket or self.control_websocket

    @property
    def is_connected(self) -> bool:
        return self.frame_websocket is not None or self.control_websocket is not None


class SessionManager:
    """Manages client WebSocket connections.

    Supports multiple clients (e.g., multiple hunting stations).
    For MVP, typically just one client is connected.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ClientSession] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        client_id: str,
        websocket: WebSocket,
        channel: ChannelType = "frame",
    ) -> ClientSession:
        async with self._lock:
            session = self._sessions.get(client_id)
            if session is None:
                session = ClientSession()
                self._sessions[client_id] = session

            if channel == "control":
                session.control_websocket = websocket
            else:
                session.frame_websocket = websocket

            logger.info("Client registered: %s (channel=%s)", client_id, channel)
            return session

    async def unregister(self, client_id: str, channel: ChannelType = "frame") -> None:
        async with self._lock:
            session = self._sessions.get(client_id)
            if session is None:
                return

            if channel == "control":
                session.control_websocket = None
            else:
                session.frame_websocket = None

            if not session.is_connected:
                del self._sessions[client_id]
                logger.info("Client unregistered: %s (all channels closed)", client_id)
            else:
                logger.info("Client channel closed: %s (channel=%s)", client_id, channel)

    def get_session(self, client_id: str) -> ClientSession | None:
        return self._sessions.get(client_id)

    @staticmethod
    def _command_transport(session: ClientSession) -> WebSocket | None:
        # Prefer control channel for minimum jitter and command consistency.
        return session.control_websocket or session.frame_websocket

    async def send_command(
        self,
        client_id: str,
        command: HIDCommandMessage,
    ) -> HIDCommandMessage:
        """Send a HID command to a specific client."""
        session = self._sessions.get(client_id)
        if session is None:
            raise ValueError(f"No client connected with id: {client_id}")
        ws = self._command_transport(session)
        if ws is None:
            raise ValueError(f"No active transport for client id: {client_id}")

        session.next_command_sequence += 1
        dispatched_at = time.time()
        cmd = command.model_copy(
            update={
                "command_id": command.command_id or str(uuid4()),
                "command_sequence": session.next_command_sequence,
                "dispatched_at_backend": dispatched_at,
            }
        )

        session.pending_commands[cmd.command_id] = dispatched_at
        session.commands_sent += 1

        try:
            await ws.send_text(cmd.model_dump_json())
        except Exception:
            session.pending_commands.pop(cmd.command_id, None)
            raise
        return cmd

    async def broadcast_command(
        self,
        command: HIDCommandMessage,
    ) -> dict[str, HIDCommandMessage]:
        """Send a command to all connected clients."""
        sent: dict[str, HIDCommandMessage] = {}
        for client_id, session in list(self._sessions.items()):
            try:
                ws = self._command_transport(session)
                if ws is None:
                    continue
                sent_cmd = await self.send_command(client_id, command)
                sent[client_id] = sent_cmd
            except Exception as e:
                logger.error("Failed to send to client %s: %s", client_id, e)
        return sent

    @property
    def connected_clients(self) -> list[str]:
        return list(self._sessions.keys())

    def get_latest_frame(self, client_id: str) -> np.ndarray | None:
        session = self._sessions.get(client_id)
        if session:
            return session.latest_frame
        return None

    def get_latest_frame_jpeg(self, client_id: str) -> tuple[bytes | None, int]:
        session = self._sessions.get(client_id)
        if session is None:
            return None, 0
        return session.latest_frame_jpeg, session.latest_frame_sequence

    def update_frame(
        self,
        client_id: str,
        frame: np.ndarray,
        jpeg_bytes: bytes,
        capture_timestamp: float | None = None,
    ) -> None:
        """Update a client's latest frame (used by both WS handler and RTSP consumer)."""
        session = self._sessions.get(client_id)
        if session is None:
            # Auto-create session for RTSP-only clients (no WS frame channel)
            session = ClientSession()
            self._sessions[client_id] = session
            logger.info("Client session created via RTSP: %s", client_id)

        session.latest_frame = frame
        session.latest_frame_jpeg = jpeg_bytes
        session.latest_frame_sequence += 1
        session.frames_received += 1
        now = time.time()
        session.last_frame_at = capture_timestamp or now
        if capture_timestamp is not None:
            session.last_frame_latency_ms = max(0.0, (now - capture_timestamp) * 1000)

    def mark_command_ack(self, client_id: str, ack: CommandAck) -> None:
        session = self._sessions.get(client_id)
        if session is None:
            return
        session.commands_acked += 1
        dispatched_at = session.pending_commands.pop(ack.command_id, None)
        if dispatched_at is not None:
            session.last_command_rtt_ms = max(0.0, (time.time() - dispatched_at) * 1000)
