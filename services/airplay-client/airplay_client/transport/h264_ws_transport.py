"""H.264 passthrough WebSocket transport — raw H.264 AUs over WebSocket.

Sends H.264 Access Units directly from the GStreamer pipe to the backend
over WebSocket, with no decode or re-encode on the client. The backend
decodes H.264 to BGR using PyAV.

This achieves near-SRT efficiency over TCP (Cloud Run compatible).
"""

import asyncio
import logging

from airplay_client.audio.base import AudioSource
from airplay_client.capture.h264_capture import H264Capture
from airplay_client.config import client_settings
from airplay_client.transport.base import MediaTransport
from airplay_client.ws_client import WebSocketClient

logger = logging.getLogger(__name__)


class H264WebSocketTransport(MediaTransport):
    """Delivers raw H.264 video (passthrough) and PCM audio over WebSocket."""

    def __init__(
        self,
        frame_ws: WebSocketClient,
        h264_capture: H264Capture,
        audio_source: AudioSource | None = None,
    ):
        self._frame_ws = frame_ws
        self._h264_capture = h264_capture
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
            asyncio.create_task(self._h264_sender_loop()),
        ]
        if self._audio_source is not None:
            self._tasks.append(asyncio.create_task(self._audio_sender_loop()))

    async def _h264_sender_loop(self) -> None:
        """Continuously read H.264 AUs and send them to the backend."""
        while self._running:
            result = await asyncio.to_thread(self._h264_capture.get_au, 0.5)
            if result is not None:
                au_bytes, is_keyframe, timestamp = result
                self.frames_captured += 1
                await self._frame_ws.send_h264_au(au_bytes, is_keyframe, timestamp)
                self.frames_sent += 1

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
        logger.info("H.264 WebSocket transport stopped")

    @property
    def is_connected(self) -> bool:
        return self._frame_ws.is_connected

    @property
    def transport_name(self) -> str:
        return "h264-ws"
