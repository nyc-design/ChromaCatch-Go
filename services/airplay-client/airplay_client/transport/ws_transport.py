"""WebSocket media transport — legacy JPEG frame + PCM audio over WebSocket.

This wraps the existing WebSocketClient frame/audio send loops into
the MediaTransport interface for use as a fallback transport mode
(e.g., on Raspberry Pi or when SRT is unavailable).
"""

import asyncio
import logging

from airplay_client.audio.base import AudioSource
from airplay_client.config import client_settings
from airplay_client.sources.base import FrameSource
from airplay_client.transport.base import MediaTransport
from airplay_client.ws_client import WebSocketClient

logger = logging.getLogger(__name__)


class WebSocketTransport(MediaTransport):
    """Delivers video frames (JPEG) and audio (PCM) over WebSocket."""

    def __init__(
        self,
        frame_ws: WebSocketClient,
        frame_source: FrameSource,
        audio_source: AudioSource | None = None,
    ):
        self._frame_ws = frame_ws
        self._frame_source = frame_source
        self._audio_source = audio_source
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self.frames_captured = 0
        self.frames_sent = 0
        self.audio_chunks_captured = 0
        self.audio_chunks_sent = 0
        self._audio_sequence = 0

    async def start(self) -> None:
        """Start frame WS connection and sender loops."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._frame_ws.connect()),
            asyncio.create_task(self._frame_sender_loop()),
        ]
        if self._audio_source is not None:
            self._tasks.append(asyncio.create_task(self._audio_sender_loop()))

    async def _frame_sender_loop(self) -> None:
        """Continuously read frames and send them to the backend."""
        while self._running:
            interval = client_settings.frame_interval_ms / 1000.0
            frame = await asyncio.to_thread(self._frame_source.get_frame, 0.5)
            if frame is not None:
                self.frames_captured += 1
                await self._frame_ws.send_frame(frame)
                self.frames_sent += 1
            await asyncio.sleep(interval)

    async def _audio_sender_loop(self) -> None:
        """Capture and send audio chunks."""
        if self._audio_source is None:
            return
        while self._running:
            chunk = await asyncio.to_thread(self._audio_source.get_chunk, 0.5)
            if chunk:
                self.audio_chunks_captured += 1
                self._audio_sequence += 1
                await self._frame_ws.send_audio_chunk(
                    pcm_bytes=chunk,
                    sequence=self._audio_sequence,
                    sample_rate=self._audio_source.sample_rate,
                    channels=self._audio_source.channels,
                )
                self.audio_chunks_sent += 1

    async def stop(self) -> None:
        """Stop sender loops and disconnect frame WS."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        await self._frame_ws.disconnect()
        logger.info("WebSocket transport stopped")

    @property
    def is_connected(self) -> bool:
        return self._frame_ws.is_connected

    @property
    def transport_name(self) -> str:
        return "websocket"
