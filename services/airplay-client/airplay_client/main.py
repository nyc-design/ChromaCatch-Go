"""ChromaCatch-Go Local Client -- main entrypoint.

Ties together:
1. Frame source (AirPlay / capture card / screen)
2. WebSocket client(s) (frames -> backend, commands <- backend)
4. ESP32 forwarder (commands -> ESP32)
"""

import asyncio
import logging
import time

from airplay_client.audio.base import AudioSource
from airplay_client.audio.factory import create_audio_source
from airplay_client.commander.esp32_client import ESP32Client
from airplay_client.config import client_settings
from airplay_client.esp32_forwarder import ESP32Forwarder
from airplay_client.sources.airplay_source import AirPlayFrameSource
from airplay_client.sources.base import FrameSource
from airplay_client.sources.factory import create_frame_source
from airplay_client.ws_client import WebSocketClient
from shared.constants import setup_logging
from shared.messages import ClientStatus, ConfigUpdate

logger = logging.getLogger(__name__)


class ChromaCatchClient:
    """Main client orchestrator."""

    def __init__(self) -> None:
        self._frame_source: FrameSource = create_frame_source()
        self._esp32 = ESP32Client()
        self._forwarder = ESP32Forwarder(self._esp32)
        self._frame_ws = WebSocketClient(
            on_hid_command=self._forwarder.handle_command,
            on_config_update=self._handle_config_update,
            backend_ws_url=client_settings.backend_ws_url,
            name="frame",
        )
        self._control_ws = WebSocketClient(
            on_hid_command=self._forwarder.handle_command,
            backend_ws_url=self._resolve_control_ws_url(),
            name="control",
        )
        self._start_time = time.time()
        self._frames_captured = 0
        self._frames_sent = 0
        self._audio_source: AudioSource | None = create_audio_source()
        self._audio_sequence = 0
        self._audio_chunks_captured = 0
        self._audio_chunks_sent = 0

    @staticmethod
    def _resolve_control_ws_url() -> str:
        if client_settings.backend_control_ws_url:
            return client_settings.backend_control_ws_url
        return client_settings.backend_ws_url.replace("/ws/client", "/ws/control")

    def _airplay_state(self) -> tuple[bool, int | None]:
        if isinstance(self._frame_source, AirPlayFrameSource):
            return self._frame_source.airplay_running, self._frame_source.airplay_pid
        return False, None

    async def _handle_config_update(self, update: ConfigUpdate) -> None:
        """Apply dynamic config updates from backend."""
        if update.jpeg_quality is not None:
            client_settings.jpeg_quality = update.jpeg_quality
        if update.max_dimension is not None:
            client_settings.max_dimension = update.max_dimension
        if update.frame_interval_ms is not None:
            client_settings.frame_interval_ms = update.frame_interval_ms
        logger.info(
            "Config updated: quality=%d, dim=%d, interval=%dms",
            client_settings.jpeg_quality,
            client_settings.max_dimension,
            client_settings.frame_interval_ms,
        )


    async def _frame_sender_loop(self) -> None:
        """Continuously read frames and send them to the backend."""
        while True:
            interval = client_settings.frame_interval_ms / 1000.0
            # FrameSource.get_frame() is a blocking call (queue read / capture poll).
            # Run it in a worker thread so the event loop remains responsive for:
            # - control WS command handling
            # - ESP32 HTTP forwarding
            # - status reporting heartbeats
            frame = await asyncio.to_thread(self._frame_source.get_frame, 0.5)
            if frame is not None:
                self._frames_captured += 1
                await self._frame_ws.send_frame(frame)
                self._frames_sent += 1
            await asyncio.sleep(interval)


    async def _status_reporter_loop(self) -> None:
        """Send periodic status updates to backend."""
        while True:
            try:
                esp32_reachable = await self._esp32.ping()
            except Exception:
                esp32_reachable = False
            esp32_ble = None
            if esp32_reachable:
                try:
                    s = await self._esp32.status()
                    esp32_ble = s.get("ble_connected")
                except Exception:
                    pass

            airplay_running, airplay_pid = self._airplay_state()

            status = ClientStatus(
                airplay_running=airplay_running,
                airplay_pid=airplay_pid,
                esp32_reachable=esp32_reachable,
                esp32_ble_connected=esp32_ble,
                frames_captured=self._frames_captured,
                frames_sent=self._frames_sent,
                capture_source=self._frame_source.source_name,
                source_running=self._frame_source.is_running,
                control_channel_connected=self._control_ws.is_connected,
                commands_sent=self._forwarder.commands_sent,
                commands_acked=self._forwarder.commands_acked,
                last_command_rtt_ms=self._forwarder.last_command_rtt_ms,
                audio_enabled=self._audio_source is not None,
                audio_source=(
                    self._audio_source.source_name
                    if self._audio_source is not None
                    else None
                ),
                audio_chunks_captured=self._audio_chunks_captured,
                audio_chunks_sent=self._audio_chunks_sent,
                uptime_seconds=time.time() - self._start_time,
            )
            if self._frame_ws.is_connected:
                await self._frame_ws.send_status(status)
            else:
                await self._control_ws.send_status(status)
            await asyncio.sleep(15)

    async def _audio_sender_loop(self) -> None:
        """Capture and send audio chunks when enabled."""
        if self._audio_source is None:
            while True:
                await asyncio.sleep(60)

        while True:
            chunk = await asyncio.to_thread(self._audio_source.get_chunk, 0.5)
            if chunk:
                self._audio_chunks_captured += 1
                self._audio_sequence += 1
                await self._frame_ws.send_audio_chunk(
                    pcm_bytes=chunk,
                    sequence=self._audio_sequence,
                    sample_rate=self._audio_source.sample_rate,
                    channels=self._audio_source.channels,
                )
                self._audio_chunks_sent += 1

    async def run(self) -> None:
        """Start all client components."""
        logger.info("ChromaCatch-Go Client starting...")

        if self._audio_source is not None:
            self._audio_source.start()
        self._frame_source.start()

        # Run WebSocket connection + frame sender + status reporter concurrently
        await asyncio.gather(
            self._frame_ws.connect(),
            self._control_ws.connect(),
            self._frame_sender_loop(),
            self._audio_sender_loop(),
            self._status_reporter_loop(),
        )

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        await self._frame_ws.disconnect()
        await self._control_ws.disconnect()
        if self._audio_source is not None:
            self._audio_source.stop()
        self._frame_source.stop()
        await self._esp32.close()


def main():
    setup_logging()
    client = ChromaCatchClient()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(client.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        loop.run_until_complete(client.shutdown())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
