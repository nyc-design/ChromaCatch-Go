"""Captures raw H.264 Access Units from UxPlay's RTP stream via GStreamer pipe.

Unlike FrameCapture (which decodes H.264 to BGR frames), this outputs raw H.264
byte-stream data for passthrough over WebSocket. No decode or re-encode on the
client — the backend decodes H.264 directly.

GStreamer pipeline: udpsrc → rtph264depay → h264parse → fdsink (stdout)
Output format: H.264 Annex B byte-stream, one Access Unit per GStreamer buffer.
"""

import logging
import os
import queue
import select
import shutil
import subprocess
import threading
import time

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)

# H.264 NAL unit start code (4-byte)
_NAL_START_CODE = b"\x00\x00\x00\x01"
# AU delimiter NAL type
_NAL_TYPE_AUD = 9
# IDR slice NAL type (keyframe)
_NAL_TYPE_IDR = 5


class H264AUParser:
    """Parses H.264 Annex B byte stream into Access Units.

    With h264parse alignment=au, each AU starts with an AU delimiter NAL
    (start code + type 9). We split the stream on AUD boundaries.
    """

    AUD_PATTERN = b"\x00\x00\x00\x01\x09"

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> list[tuple[bytes, bool]]:
        """Feed data, return list of (au_bytes, is_keyframe) tuples."""
        self._buffer += data
        aus: list[tuple[bytes, bool]] = []

        # Ensure buffer starts with AUD
        first_aud = self._buffer.find(self.AUD_PATTERN)
        if first_aud == -1:
            return aus
        if first_aud > 0:
            self._buffer = self._buffer[first_aud:]

        while True:
            # Find next AUD after the current one (skip first 5 bytes)
            idx = self._buffer.find(self.AUD_PATTERN, 5)
            if idx == -1:
                break

            au = self._buffer[:idx]
            self._buffer = self._buffer[idx:]

            is_keyframe = _has_nal_type(au, _NAL_TYPE_IDR)
            aus.append((au, is_keyframe))

        return aus


def _has_nal_type(data: bytes, nal_type: int) -> bool:
    """Check if data contains a NAL unit of the given type."""
    search_start = 0
    while True:
        idx = data.find(_NAL_START_CODE, search_start)
        if idx == -1 or idx + 4 >= len(data):
            break
        if (data[idx + 4] & 0x1F) == nal_type:
            return True
        search_start = idx + 4
    return False


class H264Capture:
    """Captures raw H.264 Access Units from UxPlay's RTP stream.

    Starts a GStreamer pipeline that receives H.264 RTP, depayloads, parses,
    and outputs Annex B byte-stream AUs to stdout. Python reads from the pipe
    and splits on AU delimiter boundaries.
    """

    def __init__(self, udp_port: int | None = None, max_queue_size: int = 30):
        self.udp_port = udp_port or settings.airplay_udp_port
        self._au_queue: queue.Queue[tuple[bytes, bool, float]] = queue.Queue(
            maxsize=max_queue_size
        )
        self._gst_proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        """Start capturing H.264 AUs in a background thread."""
        if self._running:
            logger.warning("H264 capture is already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(
            "H264 capture started (port=%d, passthrough mode)", self.udp_port
        )

    def stop(self) -> None:
        """Stop capturing."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self._gst_proc and self._gst_proc.poll() is None:
            self._gst_proc.kill()
            self._gst_proc.wait(timeout=3)
            self._gst_proc = None
        logger.info("H264 capture stopped")

    def get_au(self, timeout: float = 1.0) -> tuple[bytes, bool, float] | None:
        """Get the next H.264 Access Unit.

        Returns (au_bytes, is_keyframe, timestamp) or None on timeout.
        """
        try:
            return self._au_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _capture_loop(self) -> None:
        """Main capture loop with auto-restart on GStreamer exit."""
        logger.info(
            "Waiting for AirPlay stream on port %d (H.264 passthrough)...",
            self.udp_port,
        )

        while self._running:
            proc = None
            try:
                proc = self._start_gst_process()
                logger.info("H.264 passthrough pipeline connected!")
            except RuntimeError as e:
                logger.info(
                    "GStreamer H.264 pipe not ready: %s — retrying in 3s...", e
                )
                time.sleep(3)
                continue

            if not self._running:
                break

            parser = H264AUParser()
            last_au_at = time.time()
            saw_data = False
            reconnect_timeout_s = max(2.0, settings.airplay_reconnect_timeout_s)

            while self._running:
                if proc.poll() is not None:
                    logger.warning(
                        "GStreamer H.264 pipe exited (rc=%d), restarting",
                        proc.returncode,
                    )
                    break

                # Non-blocking read from stdout pipe
                try:
                    chunk = os.read(proc.stdout.fileno(), 131072)
                except OSError:
                    chunk = b""

                if not chunk:
                    if saw_data and (time.time() - last_au_at) > reconnect_timeout_s:
                        logger.warning(
                            "No H.264 data for %.1fs; restarting pipe",
                            reconnect_timeout_s,
                        )
                        break
                    time.sleep(0.002)
                    continue

                timestamp = time.time()
                aus = parser.feed(chunk)
                for au_bytes, is_keyframe in aus:
                    self._push_au(au_bytes, is_keyframe, timestamp)
                    last_au_at = timestamp
                    saw_data = True

            # Cleanup
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)
            self._gst_proc = None

        logger.info("H.264 capture loop stopped")

    def _start_gst_process(self) -> subprocess.Popen:
        """Start gst-launch-1.0 with H.264 Annex B output to stdout pipe."""
        gst_path = shutil.which("gst-launch-1.0")
        if not gst_path:
            raise RuntimeError("gst-launch-1.0 not found. Install GStreamer.")

        cmd = [
            gst_path,
            "-q",  # quiet: suppress text that could mix with H.264 bytes
            "-e",  # send EOS on interrupt for clean shutdown
            "udpsrc",
            f"port={self.udp_port}",
            "do-timestamp=true",
            "caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
            "!",
            "rtpjitterbuffer",
            "latency=20",
            "drop-on-latency=true",
            "!",
            "rtph264depay",
            "!",
            "h264parse",
            "config-interval=-1",
            "!",
            "video/x-h264,stream-format=byte-stream,alignment=au",
            "!",
            "fdsink",
            "fd=1",
            "sync=false",
        ]
        logger.info("Starting GStreamer H.264 pipe: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._gst_proc = proc

        # Drain stderr in background to prevent pipe buffer blocking
        threading.Thread(
            target=self._drain_stream, args=(proc.stderr,), daemon=True
        ).start()

        # Wait for first data on stdout (up to 120s for AirPlay connection)
        deadline = time.time() + 120
        logger.info(
            "GStreamer H.264 pipe pid=%d, waiting for first data...", proc.pid
        )
        while time.time() < deadline and self._running:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"GStreamer H.264 pipe exited early (rc={proc.returncode})"
                )
            try:
                ready, _, _ = select.select([proc.stdout], [], [], 0.5)
            except (ValueError, OSError):
                ready = []
            if ready:
                logger.info("GStreamer H.264 pipe: first data received")
                return proc
            time.sleep(0.5)

        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3)
        raise RuntimeError("GStreamer H.264 pipe: no data received within timeout")

    @staticmethod
    def _drain_stream(stream) -> None:
        """Read and log a subprocess stream to prevent pipe buffer blocking."""
        try:
            for line in stream:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[gst-h264] %s", text)
        except Exception:
            pass

    def _push_au(
        self, au_bytes: bytes, is_keyframe: bool, timestamp: float
    ) -> None:
        """Push an AU to the queue, dropping oldest if full."""
        if self._au_queue.full():
            try:
                self._au_queue.get_nowait()
            except queue.Empty:
                pass
        self._au_queue.put((au_bytes, is_keyframe, timestamp))
