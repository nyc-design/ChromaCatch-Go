"""SRT media transport — forwards H.264 + Opus audio via GStreamer srtsink.

The SRT transport launches a GStreamer subprocess that:
1. Receives H.264 RTP from UxPlay (localhost UDP)
2. Receives L16 audio RTP from UxPlay (localhost UDP, optional)
3. Muxes into MPEG-TS
4. Sends via SRT to the remote MediaMTX instance

Python does NOT touch frame data — the GStreamer subprocess handles everything.
This eliminates the decode->JPEG->encode cycle entirely.
"""

import asyncio
import logging
import re
import select as sel
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from airplay_client.config import client_settings as settings
from airplay_client.transport.base import MediaTransport

logger = logging.getLogger(__name__)


@dataclass
class SRTStats:
    """Parsed SRT connection statistics."""
    rtt_ms: float | None = None
    bandwidth_kbps: float | None = None
    packet_loss_pct: float | None = None


class SRTTransport(MediaTransport):
    """Forwards video + audio from UxPlay RTP to backend via SRT."""

    def __init__(
        self,
        video_udp_port: int | None = None,
        audio_udp_port: int | None = None,
        srt_url: str | None = None,
        audio_enabled: bool | None = None,
    ):
        self._video_port = video_udp_port or settings.airplay_udp_port
        self._audio_port = audio_udp_port or settings.airplay_audio_udp_port
        self._srt_url = srt_url or self._build_srt_url()
        self._audio_enabled = audio_enabled if audio_enabled is not None else settings.audio_enabled
        self._proc: subprocess.Popen | None = None
        self._running = False
        self._connected = False
        self._monitor_task: asyncio.Task | None = None
        self._stats = SRTStats()
        self._restart_count = 0

    @staticmethod
    def _build_srt_url() -> str:
        """Build the SRT URL from config."""
        if settings.srt_backend_url:
            url = settings.srt_backend_url
        else:
            # Derive from backend WS URL: ws://host:8000/ws/client -> srt://host:8890
            parsed = urlparse(settings.backend_ws_url)
            host = parsed.hostname or "localhost"
            url = f"srt://{host}:8890"

        # Append stream ID for routing within MediaMTX
        stream_id = settings.srt_stream_id or f"publish/chromacatch/{settings.client_id}"
        if "streamid=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}streamid={stream_id}"
        return url

    def _build_gst_pipeline_args(self) -> list[str]:
        """Build gst-launch-1.0 command arguments for SRT publishing."""
        gst_path = shutil.which("gst-launch-1.0")
        if not gst_path:
            raise RuntimeError("gst-launch-1.0 not found. Install GStreamer.")

        passphrase_args = []
        if settings.srt_passphrase:
            passphrase_args = [f"passphrase={settings.srt_passphrase}", "pbkeylen=16"]

        # Video: H.264 RTP from UxPlay -> passthrough (no decode) -> MPEG-TS -> SRT
        cmd = [
            gst_path, "-q", "-e",
            "udpsrc", f"port={self._video_port}", "do-timestamp=true",
            f"caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
            "!", "rtpjitterbuffer", f"latency={settings.srt_latency_ms}", "drop-on-latency=true",
            "!", "rtph264depay", "!", "h264parse", "config-interval=-1",
            "!", "queue", "max-size-time=100000000", "leaky=downstream",
            "!", "mpegtsmux", "name=mux", "alignment=7",
            "!", "srtsink",
            f"uri={self._srt_url}",
            f"latency={settings.srt_latency_ms}",
            "mode=caller",
            "wait-for-connection=true",
            *passphrase_args,
        ]

        # Audio: L16 RTP -> Opus encode -> MPEG-TS mux
        if self._audio_enabled:
            cmd.extend([
                "udpsrc", f"port={self._audio_port}",
                f"caps=application/x-rtp,media=audio,encoding-name=L16,"
                f"clock-rate={settings.audio_sample_rate},"
                f"channels={settings.audio_channels},payload=96",
                "!", "rtpL16depay", "!", "audioconvert",
                "!", "opusenc",
                f"bitrate={settings.srt_opus_bitrate}",
                "frame-size=10",
                "!", "queue",
                "!", "mux.",
            ])

        return cmd

    async def start(self) -> None:
        """Launch GStreamer SRT publisher subprocess."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        """Monitor and restart GStreamer subprocess as needed."""
        delay = 1.0
        while self._running:
            try:
                cmd = self._build_gst_pipeline_args()
                logger.info("Starting SRT transport: %s", " ".join(cmd))
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                logger.info("SRT transport pid=%d, target=%s", self._proc.pid, self._srt_url)

                delay = 1.0
                started_at = time.time()
                while self._running:
                    if self._proc.poll() is not None:
                        self._connected = False
                        logger.warning("SRT process exited (rc=%d)", self._proc.returncode)
                        break

                    # Read stderr for diagnostics and SRT stats
                    try:
                        ready, _, _ = sel.select([self._proc.stderr], [], [], 1.0)
                        if ready:
                            line = self._proc.stderr.readline()
                            text = line.decode("utf-8", errors="replace").rstrip()
                            if text:
                                logger.debug("[srt-gst] %s", text)
                                self._parse_srt_line(text)
                    except (ValueError, OSError):
                        pass

                    # Mark connected after process survives >2s
                    if not self._connected and (time.time() - started_at) > 2.0:
                        self._connected = True
                        logger.info("SRT transport connected to %s", self._srt_url)

                    await asyncio.sleep(0)

            except RuntimeError as e:
                logger.error("SRT transport error: %s", e)
            except asyncio.CancelledError:
                break

            self._connected = False
            if self._running:
                self._restart_count += 1
                logger.info("SRT transport reconnecting in %.0fs... (restart #%d)", delay, self._restart_count)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def stop(self) -> None:
        """Stop the GStreamer SRT subprocess."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                await asyncio.to_thread(self._proc.wait, timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                await asyncio.to_thread(self._proc.wait, timeout=3)
        self._proc = None
        self._connected = False
        logger.info("SRT transport stopped")

    def _parse_srt_line(self, text: str) -> None:
        """Parse SRT stats from GStreamer stderr output."""
        # GStreamer srtsink logs stats like:
        #   SRT: RTT=12.5ms, bandwidth=2500kbps, loss=0.1%
        # Also parse simpler patterns from SRT library logs
        rtt_match = re.search(r"RTT[=:]\s*([\d.]+)\s*ms", text, re.IGNORECASE)
        if rtt_match:
            self._stats.rtt_ms = float(rtt_match.group(1))
        bw_match = re.search(r"bandwidth[=:]\s*([\d.]+)\s*kbps", text, re.IGNORECASE)
        if bw_match:
            self._stats.bandwidth_kbps = float(bw_match.group(1))
        loss_match = re.search(r"loss[=:]\s*([\d.]+)\s*%", text, re.IGNORECASE)
        if loss_match:
            self._stats.packet_loss_pct = float(loss_match.group(1))

    @property
    def stats(self) -> SRTStats:
        return self._stats

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def transport_name(self) -> str:
        return "srt"
