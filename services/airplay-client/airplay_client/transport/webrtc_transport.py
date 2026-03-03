"""WebRTC media transport — forwards H.264 + Opus audio via GStreamer WHIP.

The WebRTC transport launches a GStreamer subprocess that:
1. Receives H.264 RTP from UxPlay (localhost UDP)
2. Re-payloads as RTP for WebRTC
3. Sends via WHIP POST to MediaMTX WebRTC endpoint

Python does NOT touch frame data — the GStreamer subprocess handles everything.
Lower latency than SRT due to no jitter buffer requirement.
"""

import asyncio
import logging
import shutil
import time
from urllib.parse import urlparse

from airplay_client.config import client_settings as settings
from airplay_client.transport.base import MediaTransport

logger = logging.getLogger(__name__)


class WebRTCTransport(MediaTransport):
    """Forwards video + audio from UxPlay RTP to backend via WebRTC WHIP."""

    def __init__(
        self,
        video_udp_port: int | None = None,
        audio_udp_port: int | None = None,
        whip_url: str | None = None,
        audio_enabled: bool | None = None,
    ):
        self._video_port = video_udp_port or settings.airplay_udp_port
        self._audio_port = audio_udp_port or settings.airplay_audio_udp_port
        self._whip_url = whip_url or self._build_whip_url()
        self._audio_enabled = audio_enabled if audio_enabled is not None else settings.audio_enabled
        self._proc: asyncio.subprocess.Process | None = None
        self._running = False
        self._connected = False
        self._monitor_task: asyncio.Task | None = None
        self._restart_count = 0

    @staticmethod
    def _build_whip_url() -> str:
        """Build the WHIP URL from config."""
        if settings.webrtc_whip_url:
            return settings.webrtc_whip_url

        # Derive from backend WS URL: ws://host:8000/ws/client -> http://host:8889/chromacatch/{client_id}/whip
        parsed = urlparse(settings.backend_ws_url)
        host = parsed.hostname or "localhost"
        scheme = "https" if parsed.scheme in ("wss", "https") else "http"
        stream_id = settings.srt_stream_id or f"chromacatch/{settings.client_id}"
        return f"{scheme}://{host}:8889/{stream_id}/whip"

    def _build_gst_pipeline_args(self) -> list[str]:
        """Build gst-launch-1.0 command arguments for WebRTC WHIP publishing."""
        gst_path = shutil.which("gst-launch-1.0")
        if not gst_path:
            raise RuntimeError("gst-launch-1.0 not found. Install GStreamer.")

        # Video: H.264 RTP from UxPlay -> re-payload -> WHIP
        cmd = [
            gst_path, "-q", "-e",
            "udpsrc", f"port={self._video_port}", "do-timestamp=true",
            f"caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
            "!", "rtpjitterbuffer", "latency=20", "drop-on-latency=true",
            "!", "rtph264depay", "!", "h264parse", "config-interval=-1",
            "!", "rtph264pay", "config-interval=-1", "aggregate-mode=zero-latency",
            "!", "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
            "!", "whipclientsink", "name=whip",
            f"signaller::whip-endpoint={self._whip_url}",
        ]

        # Add STUN server if configured
        if settings.webrtc_stun_server:
            cmd.append(f"signaller::stun-server={settings.webrtc_stun_server}")

        # Add TURN server if configured
        if settings.webrtc_turn_server:
            turn_url = settings.webrtc_turn_server
            if settings.webrtc_turn_username and settings.webrtc_turn_password:
                # Format: turn://user:pass@host:port
                parsed = urlparse(turn_url)
                turn_url = (
                    f"{parsed.scheme}://{settings.webrtc_turn_username}:"
                    f"{settings.webrtc_turn_password}@{parsed.hostname}"
                )
                if parsed.port:
                    turn_url += f":{parsed.port}"
            cmd.append(f"signaller::turn-server={turn_url}")

        # Audio: L16 RTP -> Opus encode -> WebRTC
        if self._audio_enabled:
            cmd.extend([
                "udpsrc", f"port={self._audio_port}",
                f"caps=application/x-rtp,media=audio,encoding-name=L16,"
                f"clock-rate={settings.audio_sample_rate},"
                f"channels={settings.audio_channels},payload=96",
                "!", "rtpL16depay", "!", "audioconvert",
                "!", "opusenc", f"bitrate={settings.srt_opus_bitrate}", "frame-size=10",
                "!", "rtpopuspay",
                "!", "application/x-rtp,media=audio,encoding-name=OPUS,payload=111,clock-rate=48000",
                "!", "whip.sink_1",
            ])

        return cmd

    async def start(self) -> None:
        """Launch GStreamer WebRTC WHIP publisher subprocess."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        """Monitor and restart GStreamer subprocess as needed."""
        delay = 1.0
        while self._running:
            try:
                cmd = self._build_gst_pipeline_args()
                logger.info("Starting WebRTC transport: %s", " ".join(cmd))
                self._proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                logger.info("WebRTC transport pid=%d, whip=%s", self._proc.pid, self._whip_url)

                delay = 1.0
                started_at = time.time()
                while self._running:
                    if self._proc.returncode is not None:
                        self._connected = False
                        logger.warning("WebRTC process exited (rc=%d)", self._proc.returncode)
                        break

                    # Non-blocking stderr read with 1s timeout
                    try:
                        line = await asyncio.wait_for(self._proc.stderr.readline(), timeout=1.0)
                        if line:
                            text = line.decode("utf-8", errors="replace").rstrip()
                            if text:
                                logger.debug("[webrtc-gst] %s", text)
                        elif self._proc.returncode is not None:
                            self._connected = False
                            logger.warning("WebRTC process exited (rc=%d)", self._proc.returncode)
                            break
                    except asyncio.TimeoutError:
                        pass
                    except (ValueError, OSError):
                        pass

                    # Mark connected after process survives >3s (ICE negotiation takes a moment)
                    if not self._connected and (time.time() - started_at) > 3.0:
                        self._connected = True
                        logger.info("WebRTC transport connected via WHIP to %s", self._whip_url)

            except RuntimeError as e:
                logger.error("WebRTC transport error: %s", e)
            except asyncio.CancelledError:
                break

            self._connected = False
            if self._running:
                self._restart_count += 1
                logger.info("WebRTC transport reconnecting in %.0fs... (restart #%d)", delay, self._restart_count)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def stop(self) -> None:
        """Stop the GStreamer WebRTC subprocess."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await asyncio.wait_for(self._proc.wait(), timeout=3)
        self._proc = None
        self._connected = False
        logger.info("WebRTC transport stopped")

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def transport_name(self) -> str:
        return "webrtc"
