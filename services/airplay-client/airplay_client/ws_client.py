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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import InvalidHandshake, InvalidMessage

from airplay_client.config import client_settings
from shared.constants import make_auth_headers
from shared.frame_codec import encode_frame
from shared.messages import (
    AudioChunk,
    BaseMessage,
    ClientStatus,
    CommandAck,
    ConfigUpdate,
    FrameMetadata,
    GameCommandMessage,
    H264FrameMetadata,
    HIDCommandMessage,
    HeartbeatPong,
    SetHIDModeMessage,
    parse_message,
)

logger = logging.getLogger(__name__)


class WebSocketClient:
    """Maintains a persistent WebSocket connection to the backend."""

    def __init__(
        self,
        on_hid_command: Callable[[HIDCommandMessage | GameCommandMessage], Awaitable[CommandAck | None]],
        on_config_update: Callable[[ConfigUpdate], Awaitable[None]] | None = None,
        on_set_hid_mode: Callable[[SetHIDModeMessage], Awaitable[None]] | None = None,
        backend_ws_url: str | None = None,
        name: str = "ws",
    ):
        self._on_hid_command = on_hid_command
        self._on_config_update = on_config_update
        self._on_set_hid_mode = on_set_hid_mode
        self._backend_ws_url = backend_ws_url or client_settings.backend_ws_url
        self._name = name
        self._ws: WebSocketClientProtocol | None = None
        self._running = False
        self._connected = False
        self._frame_sequence = 0
        self._acks_sent = 0
        self._send_lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def acks_sent(self) -> int:
        return self._acks_sent

    def _build_connect_url(self) -> str:
        parts = urlparse(self._backend_ws_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "client_id" not in query:
            query["client_id"] = client_settings.client_id
        return urlunparse(parts._replace(query=urlencode(query)))

    async def connect(self) -> None:
        """Connect to backend with auto-reconnect loop."""
        self._running = True
        delay = client_settings.ws_reconnect_delay

        while self._running:
            try:
                extra_headers = make_auth_headers(client_settings.api_key)
                connect_url = self._build_connect_url()

                ssl_ctx = None
                if connect_url.startswith("wss://"):
                    if not client_settings.ws_ssl_verify:
                        import ssl
                        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                        ssl_ctx.check_hostname = False
                        ssl_ctx.verify_mode = ssl.CERT_NONE
                    else:
                        ssl_ctx = True

                self._ws = await websockets.connect(
                    connect_url,
                    additional_headers=extra_headers,
                    ping_interval=client_settings.ws_heartbeat_interval,
                    ping_timeout=20,
                    ssl=ssl_ctx,
                )
                self._connected = True
                delay = client_settings.ws_reconnect_delay
                logger.info("[%s] Connected to backend: %s", self._name, connect_url)

                await self._receive_loop()

            except (
                websockets.ConnectionClosed,
                ConnectionRefusedError,
                OSError,
                InvalidMessage,
                InvalidHandshake,
            ) as e:
                self._connected = False
                self._ws = None
                if not self._running:
                    break
                logger.warning(
                    "[%s] WebSocket disconnected: %s. Reconnecting in %.1fs",
                    self._name,
                    e,
                    delay,
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

            if isinstance(msg, (HIDCommandMessage, GameCommandMessage)):
                ack = await self._on_hid_command(msg)
                if ack is not None:
                    await self.send_message(ack)
                    self._acks_sent += 1
            elif isinstance(msg, ConfigUpdate):
                if self._on_config_update:
                    await self._on_config_update(msg)
            elif isinstance(msg, SetHIDModeMessage):
                if self._on_set_hid_mode:
                    await self._on_set_hid_mode(msg)
            elif isinstance(msg, HeartbeatPong):
                pass
            else:
                logger.debug("Unhandled message type: %s", msg.type)

    async def send_message(self, message: BaseMessage) -> None:
        """Send an arbitrary JSON message model to backend."""
        if not self.is_connected:
            return
        async with self._send_lock:
            try:
                await self._ws.send(message.model_dump_json())
            except websockets.ConnectionClosed:
                self._connected = False

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
            sent_timestamp=time.time(),
            byte_length=len(jpeg_bytes),
        )

        async with self._send_lock:
            try:
                await self._ws.send(metadata.model_dump_json())
                await self._ws.send(jpeg_bytes)
            except websockets.ConnectionClosed:
                self._connected = False

    async def send_h264_au(
        self,
        au_bytes: bytes,
        is_keyframe: bool,
        capture_timestamp: float | None = None,
    ) -> None:
        """Send an H.264 Access Unit (metadata + binary H.264).

        Sends two WebSocket messages:
        1. JSON metadata (H264FrameMetadata)
        2. Binary H.264 AU bytes
        """
        if not self.is_connected:
            return

        ts = capture_timestamp or time.time()
        self._frame_sequence += 1
        metadata = H264FrameMetadata(
            sequence=self._frame_sequence,
            is_keyframe=is_keyframe,
            capture_timestamp=ts,
            sent_timestamp=time.time(),
            byte_length=len(au_bytes),
        )

        async with self._send_lock:
            try:
                await self._ws.send(metadata.model_dump_json())
                await self._ws.send(au_bytes)
            except websockets.ConnectionClosed:
                self._connected = False

    async def send_audio_chunk(
        self,
        pcm_bytes: bytes,
        sequence: int,
        sample_rate: int,
        channels: int,
        capture_timestamp: float | None = None,
    ) -> None:
        """Send an audio chunk (metadata + binary PCM)."""
        if not self.is_connected:
            return
        ts = capture_timestamp or time.time()
        metadata = AudioChunk(
            sequence=sequence,
            sample_rate=sample_rate,
            channels=channels,
            capture_timestamp=ts,
            sent_timestamp=time.time(),
            byte_length=len(pcm_bytes),
        )
        async with self._send_lock:
            try:
                await self._ws.send(metadata.model_dump_json())
                await self._ws.send(pcm_bytes)
            except websockets.ConnectionClosed:
                self._connected = False

    async def send_status(self, status: ClientStatus) -> None:
        """Send a status update to the backend."""
        await self.send_message(status)

    async def disconnect(self) -> None:
        """Gracefully close the WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False
