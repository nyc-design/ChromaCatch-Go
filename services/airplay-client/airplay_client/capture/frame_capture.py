"""Captures video frames from the UxPlay RTP stream."""

import logging
import os
import queue
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
        self._frame_dir: str | None = None
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


    def _start_gst_cli_process(self) -> tuple[str, int, int]:
        """Start gst-launch-1.0 with multifilesink for frame capture.

        Uses a SINGLE pipeline that writes decoded BGR frames to temp files.
        This avoids the SPS/PPS timing issue — the iPhone only sends SPS/PPS +
        IDR keyframe once at AirPlay connection time, so we must keep one
        continuous pipeline from the start. A two-phase approach (probe then
        restart) would lose the keyframe and never decode.

        On macOS, gst-launch outputs ALL verbose info to stdout (not stderr),
        so fdsink is unusable (raw frame bytes + text would mix). multifilesink
        avoids this entirely by writing to disk.

        Returns (frame_dir, width, height). Stores proc in self._gst_proc.
        """
        import tempfile

        gst_path = shutil.which("gst-launch-1.0")
        if not gst_path:
            raise RuntimeError("gst-launch-1.0 not found. Install GStreamer.")

        frame_dir = tempfile.mkdtemp(prefix="chromacatch_frames_")
        frame_pattern = os.path.join(frame_dir, "frame_%05d.raw")
        cmd = [
            gst_path,
            "udpsrc", f"port={self.udp_port}",
            f'caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000',
            "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
            "!", "videoconvert", "!", "video/x-raw,format=BGR",
            "!", "multifilesink", f"location={frame_pattern}", "max-files=30",
        ]
        logger.info("Starting GStreamer CLI: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._gst_proc = proc
        self._frame_dir = frame_dir

        # Drain stdout+stderr in background to prevent pipe blocking
        # (macOS gst-launch sends verbose info to stdout, not stderr)
        for stream in (proc.stdout, proc.stderr):
            threading.Thread(target=self._drain_stream, args=(stream,), daemon=True).start()

        # Wait for first frame file to detect resolution
        width, height = 0, 0
        deadline = time.time() + 120
        first_frame = os.path.join(frame_dir, "frame_00000.raw")
        logger.info("GStreamer pid=%d, waiting for first decoded frame...", proc.pid)

        while time.time() < deadline and self._running:
            if proc.poll() is not None:
                raise RuntimeError(f"GStreamer exited early (rc={proc.returncode})")
            if os.path.exists(first_frame):
                time.sleep(0.1)  # Let the file finish writing
                frame_bytes = os.path.getsize(first_frame)
                if frame_bytes > 0:
                    # BGR: 3 bytes per pixel. Try even widths to find valid resolution.
                    for w in range(100, 4000, 2):
                        if frame_bytes % (w * 3) == 0:
                            h = frame_bytes // (w * 3)
                            if 100 < h < 4000:
                                width, height = w, h
                                break
                    if width == 0:
                        logger.warning("Could not determine resolution from frame size %d", frame_bytes)
                    break
            time.sleep(0.5)

        if width == 0 or height == 0:
            proc.terminate()
            raise RuntimeError("Could not detect stream resolution from GStreamer")

        logger.info("Detected stream resolution: %dx%d", width, height)
        return frame_dir, width, height


    @staticmethod
    def _drain_stream(stream) -> None:
        """Read and log a subprocess stream to prevent pipe buffer blocking."""
        try:
            for line in stream:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[gst] %s", text)
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


    def _capture_loop_gst_cli(self) -> None:
        """Capture loop using gst-launch-1.0 with multifilesink.

        Reads decoded BGR frame files from a temp directory. A single
        continuous GStreamer pipeline writes frames there, ensuring we
        never miss the SPS/PPS+IDR keyframe burst.
        """
        logger.info("Waiting for AirPlay stream on port %d (GStreamer CLI)...", self.udp_port)

        frame_dir, width, height = None, 0, 0
        while self._running and frame_dir is None:
            try:
                frame_dir, width, height = self._start_gst_cli_process()
                logger.info("AirPlay stream connected via GStreamer CLI!")
            except RuntimeError as e:
                logger.info("GStreamer not ready: %s — retrying in 3s...", e)
                time.sleep(3)

        frame_size = width * height * 3
        frame_idx = 0

        while self._running:
            # Check if GStreamer process died
            if self._gst_proc and self._gst_proc.poll() is not None:
                logger.warning("GStreamer process exited (rc=%d)", self._gst_proc.returncode)
                break

            frame_path = os.path.join(frame_dir, f"frame_{frame_idx:05d}.raw")

            if not os.path.exists(frame_path):
                # Maybe we fell behind and max-files cleaned up — skip ahead
                skipped = False
                for skip in range(1, 20):
                    alt = os.path.join(frame_dir, f"frame_{frame_idx + skip:05d}.raw")
                    if os.path.exists(alt):
                        frame_idx += skip
                        frame_path = alt
                        skipped = True
                        break
                if not skipped:
                    time.sleep(0.01)
                    continue

            # Wait for file to be fully written
            try:
                fsize = os.path.getsize(frame_path)
            except OSError:
                time.sleep(0.005)
                continue
            if fsize < frame_size:
                time.sleep(0.005)
                continue

            try:
                with open(frame_path, "rb") as f:
                    data = f.read(frame_size)
                if len(data) == frame_size:
                    frame = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))
                    self._push_frame(frame)
            except (OSError, ValueError) as e:
                logger.debug("Frame %d read error: %s", frame_idx, e)

            try:
                os.remove(frame_path)
            except OSError:
                pass

            frame_idx += 1

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
        if self._frame_dir and os.path.isdir(self._frame_dir):
            shutil.rmtree(self._frame_dir, ignore_errors=True)
            self._frame_dir = None
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
