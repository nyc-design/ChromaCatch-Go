"""Captures video frames from the UxPlay RTP stream."""

import logging
import os
import queue
import re
import select
import shutil
import subprocess
import threading
import time
from enum import Enum
from pathlib import Path

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

        On macOS, gst-launch outputs verbose info to stdout (not only stderr),
        so fdsink is unusable (raw frame bytes + text would mix). multifilesink
        avoids this entirely by writing frame bytes to disk.

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
            "-v",
            "udpsrc", f"port={self.udp_port}",
            "caps=application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000",
            "!", "rtph264depay", "!", "h264parse", "!", "avdec_h264",
            "!", "videoconvert", "!", "video/x-raw,format=BGR",
            "!", "multifilesink", f"location={frame_pattern}", "max-files=30",
        ]
        logger.info("Starting GStreamer CLI: %s", " ".join(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._gst_proc = proc
        self._frame_dir = frame_dir

        # Wait for negotiated caps + first frame file.
        # We prefer parsing width/height from verbose caps output because
        # inferring from raw byte size is ambiguous (multiple valid WxH pairs).
        width, height = 0, 0
        resolution_source: str | None = None
        fallback_set_at: float | None = None
        first_frame_seen_at: float | None = None
        deadline = time.time() + 120
        logger.info("GStreamer pid=%d, waiting for first decoded frame...", proc.pid)

        while time.time() < deadline and self._running:
            if proc.poll() is not None:
                shutil.rmtree(frame_dir, ignore_errors=True)
                raise RuntimeError(f"GStreamer exited early (rc={proc.returncode})")

            try:
                ready, _, _ = select.select([proc.stdout, proc.stderr], [], [], 0.2)
            except (ValueError, OSError):
                ready = []

            for stream in ready:
                line = stream.readline()
                text = line.decode("utf-8", errors="replace").rstrip() if line else ""
                if text:
                    logger.debug("[gst] %s", text)
                    caps_resolution = self._extract_resolution_from_caps_line(text)
                    if caps_resolution:
                        caps_w, caps_h = caps_resolution
                        if (
                            resolution_source == "fallback"
                            and (caps_w != width or caps_h != height)
                        ):
                            logger.info(
                                "Caps resolution (%dx%d) overrides fallback (%dx%d)",
                                caps_w,
                                caps_h,
                                width,
                                height,
                            )
                        width, height = caps_w, caps_h
                        resolution_source = "caps"
                        logger.info(
                            "Detected stream resolution from caps: %dx%d",
                            width,
                            height,
                        )

            latest_frame = self._pick_next_frame_path(frame_dir=frame_dir, frame_idx=None)
            if latest_frame is not None:
                frame_path, _ = latest_frame
                if first_frame_seen_at is None:
                    first_frame_seen_at = time.time()

                time.sleep(0.05)  # Let the file finish writing
                if (width == 0 or height == 0) and (
                    first_frame_seen_at is not None
                    and time.time() - first_frame_seen_at >= 1.0
                ):
                    frame_bytes = self._get_stable_file_size(frame_path, timeout=0.8)
                    if frame_bytes > 0:
                        inferred = self._infer_resolution_from_frame_size(frame_bytes)
                        if inferred:
                            width, height = inferred
                            resolution_source = "fallback"
                            fallback_set_at = time.time()
                            logger.info(
                                "Detected stream resolution from frame size fallback: %dx%d",
                                width,
                                height,
                            )
                        else:
                            logger.warning(
                                "Could not determine resolution from frame size %d",
                                frame_bytes,
                            )

                if width > 0 and height > 0 and resolution_source == "caps":
                    break
                if (
                    width > 0
                    and height > 0
                    and resolution_source == "fallback"
                    and fallback_set_at is not None
                    and time.time() - fallback_set_at >= 1.0
                ):
                    break

            time.sleep(0.5)

        if width == 0 or height == 0:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            shutil.rmtree(frame_dir, ignore_errors=True)
            raise RuntimeError("Could not detect stream resolution from GStreamer")

        # Drain stdout+stderr in background for long-running pipeline.
        for stream in (proc.stdout, proc.stderr):
            threading.Thread(target=self._drain_stream, args=(stream,), daemon=True).start()

        logger.info("Using stream resolution: %dx%d", width, height)
        return frame_dir, width, height

    @staticmethod
    def _list_frame_files(frame_dir: str) -> list[tuple[int, str]]:
        """List available raw frame files sorted by frame index."""
        result: list[tuple[int, str]] = []
        for path in Path(frame_dir).glob("frame_*.raw"):
            try:
                idx = int(path.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            result.append((idx, str(path)))
        result.sort(key=lambda item: item[0])
        return result

    def _pick_next_frame_path(
        self,
        frame_dir: str,
        frame_idx: int | None,
    ) -> tuple[str, int] | None:
        """Pick the next available frame path, handling multifilesink rollovers."""
        files = self._list_frame_files(frame_dir)
        if not files:
            return None

        if frame_idx is None:
            idx, path = files[0]
            return path, idx

        for idx, path in files:
            if idx >= frame_idx:
                return path, idx

        # If all available files are older than requested index, jump to latest.
        idx, path = files[-1]
        return path, idx

    @staticmethod
    def _extract_resolution_from_caps_line(line: str) -> tuple[int, int] | None:
        """Extract width/height from a GStreamer caps line."""
        if "width=(int)" not in line or "height=(int)" not in line:
            return None
        w_match = re.search(r"width=\(int\)(\d+)", line)
        h_match = re.search(r"height=\(int\)(\d+)", line)
        if not w_match or not h_match:
            return None
        width = int(w_match.group(1))
        height = int(h_match.group(1))
        if not (100 <= width <= 5000 and 100 <= height <= 5000):
            return None
        return width, height

    @staticmethod
    def _get_stable_file_size(path: str, timeout: float = 1.0) -> int:
        """Wait until file size stabilizes, then return final size."""
        end = time.time() + timeout
        previous_size = -1
        stable_reads = 0

        while time.time() < end:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0

            if size > 0 and size == previous_size:
                stable_reads += 1
                if stable_reads >= 3:
                    return size
            else:
                stable_reads = 0
            previous_size = size
            time.sleep(0.05)

        return max(0, previous_size)


    def _infer_resolution_from_frame_size(self, frame_bytes: int) -> tuple[int, int] | None:
        """Infer width/height from raw BGR frame size.

        Multiple resolutions can map to the same byte size (W * H * 3), so this
        uses configured frame_width/frame_height as a hint and picks the closest
        candidate to that expected shape.
        """
        if frame_bytes <= 0 or frame_bytes % 3 != 0:
            return None

        expected_w = max(1, settings.frame_width)
        expected_h = max(1, settings.frame_height)
        expected_pixels = expected_w * expected_h
        expected_ratio = expected_w / expected_h

        best: tuple[float, int, int] | None = None
        max_dim = 5000

        for width in range(100, max_dim + 1, 2):
            if frame_bytes % (width * 3) != 0:
                continue

            height = frame_bytes // (width * 3)
            if not (100 <= height <= max_dim):
                continue

            ratio = width / height
            ratio_score = abs(ratio - expected_ratio)

            dim_score = (abs(width - expected_w) / expected_w) + (abs(height - expected_h) / expected_h)

            pixels = width * height
            pixels_score = abs(pixels - expected_pixels) / max(1, expected_pixels)

            score = ratio_score + pixels_score + dim_score
            if best is None or score < best[0]:
                best = (score, width, height)

        if not best:
            return None
        return best[1], best[2]


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
        frame_idx: int | None = None

        while self._running:
            # Check if GStreamer process died
            if self._gst_proc and self._gst_proc.poll() is not None:
                logger.warning("GStreamer process exited (rc=%d)", self._gst_proc.returncode)
                break

            next_frame = self._pick_next_frame_path(frame_dir=frame_dir, frame_idx=frame_idx)
            if next_frame is None:
                time.sleep(0.01)
                continue
            frame_path, frame_idx = next_frame

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

            frame_idx = frame_idx + 1

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
