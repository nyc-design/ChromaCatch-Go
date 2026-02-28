"""WebSocket client for connecting to the remote backend.

Maintains a persistent bidirectional WebSocket connection:
- Sends JPEG-encoded frames upstream to the backend
- Receives HID commands downstream from the backend
- Auto-reconnects with exponential backoff on disconnection
"""

import asyncio
import logging
import time
from typing import Awaitable, Callable

import websockets
from websockets.client import WebSocketClientProtocol

from airplay_client.config import client_settings
from shared.constants import make_auth_headers
from shared.frame_codec import encode_frame
from shared.messages import ClientStatus, ConfigUpdate, FrameMetadata, HIDCommandMessage, HeartbeatPong, parse_message

logger = logging.getLogger(__name__)


class WebSocketClient:
    """Maintains a persistent WebSocket connection to the backend."""

    def __init__(self, on_hid_command: Callable[[HIDCommandMessage], Awaitable[None]], on_config_update: Callable[[ConfigUpdate], Awaitable[None]] | None = None, backend_ws_url: str | None = None):
        self._on_hid_command = on_hid_command
        self._on_config_update = on_config_update
        self._backend_ws_url = backend_ws_url or client_settings.backend_ws_url
        self._ws: WebSocketClientProtocol | None = None
        self._running = False
        self._connected = False
        self._frame_sequence = 0
        self._send_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    async def connect(self) -> None:
        """Connect to backend with auto-reconnect loop."""
        self._running = True
        delay = client_settings.ws_reconnect_delay

        while self._running:
            try:
                extra_headers = make_auth_headers(client_settings.api_key)

                self._ws = await websockets.connect(
                    self._backend_ws_url,
                    additional_headers=extra_headers,
                    ping_interval=client_settings.ws_heartbeat_interval,
                    ping_timeout=20,
                )
                self._connected = True
                delay = client_settings.ws_reconnect_delay
                logger.info("Connected to backend: %s", self._backend_ws_url)

                await self._receive_loop()

            except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                self._connected = False
                self._ws = None
                if not self._running:
                    break
                logger.warning(
                    "WebSocket disconnected: %s. Reconnecting in %.1fs", e, delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, client_settings.ws_reconnect_max_delay)

    async def _receive_loop(self) -> None:
        """Listen for messages from the backend."""
        async for raw_message in self._ws:
            if isinstance(raw_message, bytes):
                logger.warning("Unexpected binary message from backend")
                continue

            try:
                msg = parse_message(raw_message)
            except Exception:
                logger.error("Failed to parse message: %s", raw_message[:200])
                continue

            if isinstance(msg, HIDCommandMessage):
                await self._on_hid_command(msg)
            elif isinstance(msg, ConfigUpdate):
                if self._on_config_update:
                    await self._on_config_update(msg)
            elif isinstance(msg, HeartbeatPong):
                pass
            else:
                logger.debug("Unhandled message type: %s", msg.type)

    async def send_frame(self, frame_bgr, capture_timestamp: float | None = None) -> None:
        """Encode and send a frame to the backend.

        Sends two WebSocket messages:
        1. JSON metadata (FrameMetadata)
        2. Binary JPEG bytes
        """
        if not self.is_connected:
            return

        ts = capture_timestamp or time.time()
        jpeg_bytes, w, h = encode_frame(frame_bgr, quality=client_settings.jpeg_quality, max_dimension=client_settings.max_dimension)

        self._frame_sequence += 1
        metadata = FrameMetadata(
            sequence=self._frame_sequence,
            width=w,
            height=h,
            jpeg_quality=client_settings.jpeg_quality,
            capture_timestamp=ts,
            byte_length=len(jpeg_bytes),
        )

        async with self._send_lock:
            try:
                await self._ws.send(metadata.model_dump_json())
                await self._ws.send(jpeg_bytes)
            except websockets.ConnectionClosed:
                self._connected = False

    async def send_status(self, status: ClientStatus) -> None:
        """Send a status update to the backend."""
        if not self.is_connected:
            return
        async with self._send_lock:
            try:
                await self._ws.send(status.model_dump_json())
            except websockets.ConnectionClosed:
                self._connected = False

    async def disconnect(self) -> None:
        """Gracefully close the WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False
