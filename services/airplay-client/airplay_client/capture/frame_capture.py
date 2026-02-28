"""Captures video frames from the UxPlay RTP stream."""

import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from enum import Enum

import cv2
import numpy as np

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


class CaptureBackend(str, Enum):
    GSTREAMER = "gstreamer"
    FFMPEG = "ffmpeg"


class FrameCapture:
    """Captures frames from the AirPlay RTP stream.

    Supports two backends:
    - GStreamer: OpenCV VideoCapture with GStreamer pipeline (preferred, Linux)
    - FFmpeg: FFmpeg subprocess piping raw frames (fallback, macOS)

    Frames are pushed into a thread-safe queue for consumption.
    """

    def __init__(self, udp_port: int | None = None, backend: CaptureBackend | None = None, max_queue_size: int = 5):
        self.udp_port = udp_port or settings.airplay_udp_port
        self.backend = backend or self._detect_backend()
        self.frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue_size)
        self._capture: cv2.VideoCapture | None = None
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._sdp_path: str | None = None


    @staticmethod
    def _detect_backend() -> CaptureBackend:
        """Auto-detect the best available backend."""
        build_info = cv2.getBuildInformation()
        if "GStreamer" in build_info:
            for line in build_info.split("\n"):
                if "GStreamer" in line and "YES" in line:
                    return CaptureBackend.GSTREAMER
        return CaptureBackend.FFMPEG


    def _build_gstreamer_pipeline(self) -> str:
        return (
            f'udpsrc port={self.udp_port} '
            f'caps="application/x-rtp,media=video,encoding-name=H264,payload=96" '
            f'! rtph264depay ! h264parse ! avdec_h264 '
            f'! videoconvert ! video/x-raw,format=BGR '
            f'! appsink drop=true sync=false max-buffers=2'
        )


    def _build_sdp_file(self) -> str:
        """Create a temporary SDP file describing the RTP H264 stream."""
        sdp_content = (
            "v=0\r\n"
            "o=- 0 0 IN IP4 127.0.0.1\r\n"
            "s=UxPlay\r\n"
            "c=IN IP4 127.0.0.1\r\n"
            "t=0 0\r\n"
            f"m=video {self.udp_port} RTP/AVP 96\r\n"
            "a=rtpmap:96 H264/90000\r\n"
        )
        fd, path = tempfile.mkstemp(suffix=".sdp", prefix="chromacatch_")
        with os.fdopen(fd, "w") as f:
            f.write(sdp_content)
        self._sdp_path = path
        return path


    def _start_ffmpeg_process(self) -> tuple[subprocess.Popen, int, int]:
        """Start FFmpeg subprocess to decode RTP H264 → raw BGR frames.

        Returns (process, width, height). Resolution is auto-detected from
        FFmpeg's stderr output.
        """
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found. Install it: brew install ffmpeg")

        sdp_path = self._build_sdp_file()
        cmd = [
            "ffmpeg",
            "-loglevel", "info",
            "-protocol_whitelist", "file,crypto,data,rtp,udp",
            "-analyzeduration", "10000000",
            "-probesize", "10000000",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-i", sdp_path,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "pipe:1",
        ]
        logger.info("Starting FFmpeg subprocess: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**8)

        # Read stderr line-by-line until we find the stream resolution
        width, height = 0, 0
        deadline = time.time() + 30
        buf = b""
        while time.time() < deadline:
            byte = proc.stderr.read(1)
            if not byte:
                break
            buf += byte
            if byte in (b"\n", b"\r"):
                line = buf.decode("utf-8", errors="replace")
                match = re.search(r"(\d{3,5})x(\d{3,5})", line)
                if match and "Video" in line:
                    width, height = int(match.group(1)), int(match.group(2))
                    break
                buf = b""

        if width == 0 or height == 0:
            proc.kill()
            raise RuntimeError("Could not detect stream resolution from FFmpeg")

        logger.info("Detected stream resolution: %dx%d", width, height)

        # Drain remaining stderr in background to prevent pipe blocking
        threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True).start()

        return proc, width, height


    @staticmethod
    def _drain_stderr(proc: subprocess.Popen) -> None:
        """Read and discard stderr to prevent FFmpeg from blocking."""
        try:
            while proc.poll() is None:
                proc.stderr.read(4096)
        except Exception:
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


    def _capture_loop_ffmpeg(self) -> None:
        """Capture loop using FFmpeg subprocess piping raw frames."""
        logger.info("Waiting for AirPlay stream on port %d (FFmpeg)...", self.udp_port)

        proc, width, height = None, 0, 0
        while self._running and proc is None:
            try:
                proc, width, height = self._start_ffmpeg_process()
                self._ffmpeg_proc = proc
                logger.info("AirPlay stream connected via FFmpeg!")
            except RuntimeError as e:
                logger.debug("FFmpeg not ready: %s, retrying...", e)
                time.sleep(3)

        frame_size = width * height * 3
        while self._running and proc is not None:
            raw = proc.stdout.read(frame_size)
            if len(raw) != frame_size:
                logger.warning("FFmpeg stream ended (got %d/%d bytes)", len(raw), frame_size)
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            self._push_frame(frame)

        logger.info("FFmpeg capture loop stopped")


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
        target = self._capture_loop_gstreamer if self.backend == CaptureBackend.GSTREAMER else self._capture_loop_ffmpeg
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
        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
            self._ffmpeg_proc.kill()
            self._ffmpeg_proc.wait(timeout=3)
            self._ffmpeg_proc = None
        if self._sdp_path and os.path.exists(self._sdp_path):
            os.unlink(self._sdp_path)
            self._sdp_path = None
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
