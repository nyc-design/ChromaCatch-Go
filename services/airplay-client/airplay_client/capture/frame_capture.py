"""Captures video frames from the UxPlay RTP stream."""

import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from enum import Enum

import cv2
import numpy as np

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


class CaptureBackend(str, Enum):
    GSTREAMER = "gstreamer"
    GSTREAMER_CLI = "gstreamer_cli"


class FrameCapture:
    """Captures frames from the AirPlay RTP stream.

    Supports two backends:
    - GStreamer: OpenCV VideoCapture with GStreamer pipeline (Linux with OpenCV GStreamer)
    - GStreamer CLI: gst-launch-1.0 subprocess piping raw frames (macOS, or any system
      with GStreamer installed but OpenCV lacking GStreamer support)

    Frames are pushed into a thread-safe queue for consumption.
    """

    def __init__(self, udp_port: int | None = None, backend: CaptureBackend | None = None, max_queue_size: int = 5):
        self.udp_port = udp_port or settings.airplay_udp_port
        self.backend = backend or self._detect_backend()
        self.frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue_size)
        self._capture: cv2.VideoCapture | None = None
        self._gst_proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False


    @staticmethod
    def _detect_backend() -> CaptureBackend:
        """Auto-detect the best available backend."""
        build_info = cv2.getBuildInformation()
        if "GStreamer" in build_info:
            for line in build_info.split("\n"):
                if "GStreamer" in line and "YES" in line:
                    return CaptureBackend.GSTREAMER
        # Fall back to gst-launch-1.0 CLI subprocess
        if shutil.which("gst-launch-1.0"):
            return CaptureBackend.GSTREAMER_CLI
        raise RuntimeError(
            "No capture backend available. Install GStreamer: "
            "brew install gstreamer (macOS) or apt install gstreamer1.0-tools (Linux)"
        )


    def _build_gstreamer_pipeline(self) -> str:
        return (
            f'udpsrc port={self.udp_port} '
            f'caps="application/x-rtp,media=video,encoding-name=H264,payload=96" '
            f'! rtph264depay ! h264parse ! avdec_h264 '
            f'! videoconvert ! video/x-raw,format=BGR '
            f'! appsink drop=true sync=false max-buffers=2'
        )


    def _start_gst_cli_process(self) -> tuple[subprocess.Popen, int, int]:
        """Start gst-launch-1.0 subprocess to decode RTP H264 → raw BGR frames.

        Returns (process, width, height). Resolution is auto-detected from
        GStreamer's stderr output (the caps negotiation messages).

        On macOS, gst-launch fully buffers stderr when piped (not a TTY),
        so caps info never arrives. We use a pseudo-TTY for stderr to force
        line buffering.
        """
        gst_path = shutil.which("gst-launch-1.0")
        if not gst_path:
            raise RuntimeError("gst-launch-1.0 not found. Install GStreamer.")

        cmd = [
            gst_path, "-v",
            "udpsrc", f"port={self.udp_port}",
            f'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000',
            "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
            "!", "videoconvert", "!", "video/x-raw,format=BGR",
            "!", "fdsink", "fd=1",
        ]
        logger.info("Starting GStreamer CLI: %s", " ".join(cmd))

        # Use a pseudo-TTY for stderr so gst-launch line-buffers its output.
        # Without this, macOS fully buffers stderr when piped, and the caps
        # negotiation messages never arrive for resolution detection.
        import pty
        stderr_master, stderr_slave = pty.openpty()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_slave, bufsize=10**8)
        os.close(stderr_slave)  # Only the child needs the slave end

        stderr_stream = os.fdopen(stderr_master, "rb", 0)  # Unbuffered read

        # Read stderr line-by-line until we find the negotiated resolution
        # GStreamer -v outputs caps like: video/x-raw, format=BGR, width=1920, height=1080
        width, height = 0, 0
        deadline = time.time() + 120  # Wait up to 2 minutes for iPhone to connect
        buf = b""
        logger.info("GStreamer CLI pid=%d, waiting for stream...", proc.pid)
        while time.time() < deadline and self._running:
            try:
                byte = stderr_stream.read(1)
            except OSError:
                rc = proc.poll()
                logger.debug("GStreamer stderr read error, exit code: %s", rc)
                break
            if not byte:
                rc = proc.poll()
                logger.debug("GStreamer stderr EOF, exit code: %s", rc)
                break
            buf += byte
            if byte in (b"\n", b"\r"):
                line = buf.decode("utf-8", errors="replace").rstrip()
                if line:
                    logger.debug("[gst] %s", line)
                # Look for negotiated caps: width=(int)1920, height=(int)1080
                w_match = re.search(r"width=\(int\)(\d+)", line)
                h_match = re.search(r"height=\(int\)(\d+)", line)
                if w_match and h_match and "BGR" in line:
                    width, height = int(w_match.group(1)), int(h_match.group(1))
                    break
                buf = b""

        if width == 0 or height == 0:
            rc = proc.poll()
            logger.warning("GStreamer failed to detect resolution (exit code: %s)", rc)
            stderr_stream.close()
            proc.kill()
            raise RuntimeError("Could not detect stream resolution from GStreamer")

        logger.info("Detected stream resolution: %dx%d", width, height)

        # Drain remaining stderr in background to prevent pipe blocking
        threading.Thread(target=self._drain_stderr_fd, args=(stderr_stream,), daemon=True).start()

        return proc, width, height


    @staticmethod
    def _drain_stderr(proc: subprocess.Popen) -> None:
        """Read and log stderr to prevent blocking."""
        try:
            for line in proc.stderr:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[gst] %s", text)
        except Exception:
            pass

    @staticmethod
    def _drain_stderr_fd(stderr_stream) -> None:
        """Read and log a PTY stderr stream to prevent blocking."""
        buf = b""
        try:
            while True:
                byte = stderr_stream.read(1)
                if not byte:
                    break
                buf += byte
                if byte in (b"\n", b"\r"):
                    line = buf.decode("utf-8", errors="replace").rstrip()
                    if line:
                        logger.debug("[gst] %s", line)
                    buf = b""
        except OSError:
            pass
        finally:
            try:
                stderr_stream.close()
            except OSError:
                pass


    def _capture_loop_gstreamer(self) -> None:
        """Capture loop using OpenCV GStreamer backend."""
        logger.info("Waiting for AirPlay stream on port %d...", self.udp_port)
        while self._running and self._capture is None:
            try:
                pipeline = self._build_gstreamer_pipeline()
                logger.info("Using GStreamer pipeline: %s", pipeline)
                cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                if not cap.isOpened():
                    raise RuntimeError("GStreamer capture failed to open")
                self._capture = cap
                logger.info("AirPlay stream connected!")
            except RuntimeError:
                time.sleep(3)

        while self._running:
            if self._capture is None:
                break
            ret, frame = self._capture.read()
            if not ret:
                continue
            self._push_frame(frame)

        logger.info("GStreamer capture loop stopped")


    def _capture_loop_gst_cli(self) -> None:
        """Capture loop using gst-launch-1.0 subprocess piping raw frames."""
        logger.info("Waiting for AirPlay stream on port %d (GStreamer CLI)...", self.udp_port)

        proc, width, height = None, 0, 0
        while self._running and proc is None:
            try:
                proc, width, height = self._start_gst_cli_process()
                self._gst_proc = proc
                logger.info("AirPlay stream connected via GStreamer CLI!")
            except RuntimeError as e:
                logger.info("GStreamer not ready: %s — retrying in 3s...", e)
                time.sleep(3)

        frame_size = width * height * 3
        while self._running and proc is not None:
            raw = proc.stdout.read(frame_size)
            if len(raw) != frame_size:
                logger.warning("GStreamer stream ended (got %d/%d bytes)", len(raw), frame_size)
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            self._push_frame(frame)

        logger.info("GStreamer CLI capture loop stopped")


    def _push_frame(self, frame: np.ndarray) -> None:
        """Push a frame to the queue, dropping oldest if full."""
        if self.frame_queue.full():
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                pass
        self.frame_queue.put(frame)


    def start(self) -> None:
        """Start capturing frames in a background thread."""
        if self._running:
            logger.warning("Frame capture is already running")
            return

        self._running = True
        if self.backend == CaptureBackend.GSTREAMER:
            target = self._capture_loop_gstreamer
        else:
            target = self._capture_loop_gst_cli
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        logger.info("Frame capture started (backend=%s, port=%d)", self.backend.value, self.udp_port)


    def stop(self) -> None:
        """Stop capturing frames."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self._capture:
            self._capture.release()
            self._capture = None
        if self._gst_proc and self._gst_proc.poll() is None:
            self._gst_proc.kill()
            self._gst_proc.wait(timeout=3)
            self._gst_proc = None
        logger.info("Frame capture stopped")


    def get_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        """Get the latest frame, blocking up to timeout seconds."""
        try:
            return self.frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None


    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()
