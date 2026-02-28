"""ChromaCatch-Go Local Client -- main entrypoint.

Ties together:
1. AirPlay manager (UxPlay subprocess)
2. Frame capture (OpenCV from RTP)
3. WebSocket client (frames -> backend, commands <- backend)
4. ESP32 forwarder (commands -> ESP32)
"""

import asyncio
import logging
import time

from airplay_client.capture.airplay_manager import AirPlayManager
from airplay_client.capture.frame_capture import FrameCapture
from airplay_client.commander.esp32_client import ESP32Client
from airplay_client.config import client_settings
from airplay_client.esp32_forwarder import ESP32Forwarder
from airplay_client.ws_client import WebSocketClient
from shared.constants import setup_logging
from shared.messages import ClientStatus, ConfigUpdate

logger = logging.getLogger(__name__)


class ChromaCatchClient:
    """Main client orchestrator."""

    def __init__(self) -> None:
        self._airplay = AirPlayManager()
        self._frame_capture = FrameCapture()
        self._esp32 = ESP32Client()
        self._forwarder = ESP32Forwarder(self._esp32)
        self._ws_client = WebSocketClient(
            on_hid_command=self._forwarder.handle_command,
            on_config_update=self._handle_config_update,
        )
        self._start_time = time.time()
        self._frames_captured = 0
        self._frames_sent = 0

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
            frame = self._frame_capture.get_frame(timeout=0.5)
            if frame is not None:
                self._frames_captured += 1
                await self._ws_client.send_frame(frame)
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

            status = ClientStatus(
                airplay_running=self._airplay.is_running,
                airplay_pid=self._airplay.pid,
                esp32_reachable=esp32_reachable,
                esp32_ble_connected=esp32_ble,
                frames_captured=self._frames_captured,
                frames_sent=self._frames_sent,
                uptime_seconds=time.time() - self._start_time,
            )
            await self._ws_client.send_status(status)
            await asyncio.sleep(15)

    async def run(self) -> None:
        """Start all client components."""
        logger.info("ChromaCatch-Go Client starting...")

        # Start frame capture FIRST — it must be listening on the UDP port
        # before UxPlay connects, because the iPhone only sends SPS/PPS +
        # IDR keyframe once at connection time.  If nobody is listening,
        # those packets are lost and the decoder can never start.
        self._frame_capture.start()

        # Start AirPlay receiver (UxPlay)
        self._airplay.start()

        # Run WebSocket connection + frame sender + status reporter concurrently
        await asyncio.gather(
            self._ws_client.connect(),
            self._frame_sender_loop(),
            self._status_reporter_loop(),
        )

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        await self._ws_client.disconnect()
        self._frame_capture.stop()
        self._airplay.stop()
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
