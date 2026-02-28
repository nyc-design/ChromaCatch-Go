"""Captures video frames from the UxPlay RTP stream."""

import logging
import queue
import threading
from enum import Enum

import cv2
import numpy as np

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


class CaptureBackend(str, Enum):
    GSTREAMER = "gstreamer"
    FFMPEG = "ffmpeg"


class FrameCapture:
    """Captures frames from the AirPlay RTP stream via OpenCV.

    Supports two backends:
    - GStreamer: OpenCV VideoCapture with GStreamer pipeline (preferred)
    - FFmpeg: OpenCV VideoCapture with FFmpeg subprocess (fallback)

    Frames are pushed into a thread-safe queue for consumption by the CV pipeline.
    """

    def __init__(self, udp_port: int | None = None, backend: CaptureBackend | None = None, max_queue_size: int = 5):
        self.udp_port = udp_port or settings.airplay_udp_port
        self.backend = backend or self._detect_backend()
        self.frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue_size)
        self._capture: cv2.VideoCapture | None = None
        self._thread: threading.Thread | None = None
        self._running = False


    @staticmethod
    def _detect_backend() -> CaptureBackend:
        """Auto-detect the best available backend."""
        build_info = cv2.getBuildInformation()
        if "GStreamer" in build_info:
            # Extract the line containing GStreamer and check for YES
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


    def _build_ffmpeg_pipeline(self) -> str:
        return (
            f'udp://127.0.0.1:{self.udp_port}'
        )


    def _create_capture(self) -> cv2.VideoCapture:
        """Create the OpenCV VideoCapture with the appropriate backend."""
        if self.backend == CaptureBackend.GSTREAMER:
            pipeline = self._build_gstreamer_pipeline()
            logger.info("Using GStreamer pipeline: %s", pipeline)
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        else:
            pipeline = self._build_ffmpeg_pipeline()
            logger.info("Using FFmpeg pipeline: %s", pipeline)
            cap = cv2.VideoCapture(pipeline, cv2.CAP_FFMPEG)

        if not cap.isOpened():
            raise RuntimeError(
                f"Failed to open video capture with {self.backend.value} backend. "
                "Ensure UxPlay is running and forwarding to the correct port."
            )
        return cap


    def _capture_loop(self) -> None:
        """Background thread that reads frames and pushes to queue."""
        logger.info("Frame capture loop started")
        while self._running:
            if self._capture is None:
                break
            ret, frame = self._capture.read()
            if not ret:
                logger.debug("No frame received, retrying...")
                continue

            # Drop oldest frame if queue is full (keep latest)
            if self.frame_queue.full():
                try:
                    self.frame_queue.get_nowait()
                except queue.Empty:
                    pass

            self.frame_queue.put(frame)

        logger.info("Frame capture loop stopped")


    def start(self) -> None:
        """Start capturing frames in a background thread."""
        if self._running:
            logger.warning("Frame capture is already running")
            return

        self._capture = self._create_capture()
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Frame capture started (backend=%s, port=%d)",
                     self.backend.value, self.udp_port)


    def stop(self) -> None:
        """Stop capturing frames."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        if self._capture:
            self._capture.release()
            self._capture = None
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
