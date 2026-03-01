"""ChromaCatch-Go Local Client -- main entrypoint.

Ties together:
1. Frame source (AirPlay / capture card / screen)
2. Media transport (SRT or WebSocket) for video/audio delivery
3. WebSocket control channel (commands + status, always active)
4. ESP32 forwarder (commands -> ESP32)
"""

import asyncio
import logging
import signal
import time

from airplay_client.audio.base import AudioSource
from airplay_client.audio.factory import create_audio_source
from airplay_client.capture.process_cleanup import cleanup_stale_airplay_processes
from airplay_client.commander.esp32_client import ESP32Client
from airplay_client.config import client_settings
from airplay_client.esp32_forwarder import ESP32Forwarder
from airplay_client.runtime_lock import SingleInstanceLock
from airplay_client.sources.airplay_source import AirPlayFrameSource
from airplay_client.sources.base import FrameSource
from airplay_client.sources.factory import create_frame_source
from airplay_client.transport.base import MediaTransport
from airplay_client.transport.factory import create_media_transport
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
        self._audio_source: AudioSource | None = create_audio_source()

        # Control channel: always WebSocket (commands, status, ACKs)
        self._control_ws = WebSocketClient(
            on_hid_command=self._forwarder.handle_command,
            backend_ws_url=self._resolve_control_ws_url(),
            name="control",
        )

        # Media transport: SRT (low-latency) or WebSocket (fallback)
        # Frame WS is needed for "websocket" and "srt-failover" modes
        self._frame_ws: WebSocketClient | None = None
        if client_settings.transport_mode in ("websocket", "srt-failover"):
            self._frame_ws = WebSocketClient(
                on_hid_command=self._forwarder.handle_command,
                on_config_update=self._handle_config_update,
                backend_ws_url=client_settings.backend_ws_url,
                name="frame",
            )
        self._transport: MediaTransport = create_media_transport(
            frame_source=self._frame_source,
            audio_source=self._audio_source,
            frame_ws=self._frame_ws,
        )

        self._start_time = time.time()

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

            # Pull frame/audio counters from transport (WS tracks these; SRT does not)
            frames_captured = getattr(self._transport, "frames_captured", 0)
            frames_sent = getattr(self._transport, "frames_sent", 0)
            audio_chunks_captured = getattr(self._transport, "audio_chunks_captured", 0)
            audio_chunks_sent = getattr(self._transport, "audio_chunks_sent", 0)

            # Pull SRT stats if available
            srt_stats = getattr(self._transport, "stats", None)
            srt_rtt = srt_stats.rtt_ms if srt_stats else None
            srt_bw = srt_stats.bandwidth_kbps if srt_stats else None
            srt_loss = srt_stats.packet_loss_pct if srt_stats else None

            status = ClientStatus(
                airplay_running=airplay_running,
                airplay_pid=airplay_pid,
                esp32_reachable=esp32_reachable,
                esp32_ble_connected=esp32_ble,
                frames_captured=frames_captured,
                frames_sent=frames_sent,
                capture_source=self._frame_source.source_name,
                source_running=self._frame_source.is_running,
                control_channel_connected=self._control_ws.is_connected,
                transport_mode=self._transport.transport_name,
                transport_connected=self._transport.is_connected,
                commands_sent=self._forwarder.commands_sent,
                commands_acked=self._forwarder.commands_acked,
                last_command_rtt_ms=self._forwarder.last_command_rtt_ms,
                audio_enabled=self._audio_source is not None,
                audio_source=(
                    self._audio_source.source_name
                    if self._audio_source is not None
                    else None
                ),
                audio_chunks_captured=audio_chunks_captured,
                audio_chunks_sent=audio_chunks_sent,
                uptime_seconds=time.time() - self._start_time,
                srt_rtt_ms=srt_rtt,
                srt_bandwidth_kbps=srt_bw,
                srt_packet_loss_pct=srt_loss,
            )
            # Send status via frame WS if available, otherwise control WS
            if self._frame_ws is not None and self._frame_ws.is_connected:
                await self._frame_ws.send_status(status)
            else:
                await self._control_ws.send_status(status)
            await asyncio.sleep(15)

    async def run(self) -> None:
        """Start all client components."""
        logger.info(
            "ChromaCatch-Go Client starting (transport=%s)...",
            client_settings.transport_mode,
        )

        if (
            isinstance(self._frame_source, AirPlayFrameSource)
            and client_settings.cleanup_stale_airplay_processes
        ):
            cleanup_stale_airplay_processes(
                video_port=client_settings.airplay_udp_port,
                audio_port=client_settings.airplay_audio_udp_port,
            )

        # In SRT mode, GStreamer reads directly from RTP — no Python frame source needed.
        # In WS and failover modes, we need the frame source for JPEG fallback.
        if client_settings.transport_mode in ("websocket", "srt-failover"):
            if self._audio_source is not None:
                self._audio_source.start()
            self._frame_source.start()

        # Run transport + control channel + status reporter concurrently
        await asyncio.gather(
            self._transport.start(),
            self._control_ws.connect(),
            self._status_reporter_loop(),
        )

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        await self._transport.stop()
        await self._control_ws.disconnect()
        if self._audio_source is not None:
            self._audio_source.stop()
        if client_settings.transport_mode in ("websocket", "srt-failover"):
            self._frame_source.stop()
        await self._esp32.close()


def main():
    setup_logging()
    runtime_lock = SingleInstanceLock(client_settings.client_id)
    if not runtime_lock.acquire():
        logger.error(
            "Another chromacatch-client instance is already running for client_id=%s",
            client_settings.client_id,
        )
        raise SystemExit(2)

    client = ChromaCatchClient()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    stopping = False

    def _request_stop() -> None:
        nonlocal stopping
        stopping = True
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(client.run())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except asyncio.CancelledError:
        logger.info("Client cancelled")
    except Exception:
        logger.exception("Client crashed")
    finally:
        try:
            loop.run_until_complete(client.shutdown())
        except Exception:
            logger.exception("Client shutdown encountered an error")
        loop.close()
        runtime_lock.release()


if __name__ == "__main__":
    main()
