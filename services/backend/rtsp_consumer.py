"""RTSP frame consumer — reads frames from MediaMTX for the CV pipeline.

When a client connects via SRT, MediaMTX makes the stream available as
RTSP on localhost. This consumer reads frames from that RTSP stream and
feeds them into the SessionManager, just like the WebSocket handler does
for WS-transported frames.
"""

import asyncio
import logging
import time

import cv2
import numpy as np

from backend.config import backend_settings as settings
from backend.session_manager import SessionManager

logger = logging.getLogger(__name__)


class RTSPFrameConsumer:
    """Consumes video frames from MediaMTX local RTSP for CV pipeline."""

    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager
        self._consumers: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        """Start the consumer (begins polling for new RTSP streams)."""
        if not settings.rtsp_consumer_enabled:
            logger.info("RTSP consumer disabled (CC_BACKEND_RTSP_CONSUMER_ENABLED=false)")
            return
        self._running = True
        logger.info("RTSP consumer started, base URL: %s", settings.rtsp_base_url)

    async def stop(self) -> None:
        """Stop all consumers."""
        self._running = False
        for client_id, task in self._consumers.items():
            task.cancel()
        self._consumers.clear()
        logger.info("RTSP consumer stopped")

    async def add_stream(self, client_id: str, stream_path: str | None = None) -> None:
        """Start consuming frames from an RTSP stream for a client.

        Args:
            client_id: The client ID to associate frames with.
            stream_path: RTSP path (e.g., "chromacatch/my-client").
                         Defaults to "chromacatch/{client_id}".
        """
        if client_id in self._consumers:
            logger.warning("RTSP consumer already running for %s", client_id)
            return

        path = stream_path or f"chromacatch/{client_id}"
        rtsp_url = f"{settings.rtsp_base_url}/{path}"
        logger.info("Starting RTSP consumer for %s: %s", client_id, rtsp_url)
        task = asyncio.create_task(self._consume_loop(client_id, rtsp_url))
        self._consumers[client_id] = task

    async def remove_stream(self, client_id: str) -> None:
        """Stop consuming frames for a client."""
        task = self._consumers.pop(client_id, None)
        if task:
            task.cancel()
            logger.info("RTSP consumer stopped for %s", client_id)

    async def _consume_loop(self, client_id: str, rtsp_url: str) -> None:
        """Read frames from RTSP and feed into session manager."""
        delay = 1.0
        while self._running:
            cap = None
            try:
                cap = await asyncio.to_thread(self._open_capture, rtsp_url)
                if cap is None or not cap.isOpened():
                    raise RuntimeError(f"Failed to open RTSP: {rtsp_url}")

                logger.info("RTSP stream connected for %s", client_id)
                delay = 1.0
                sequence = 0

                while self._running:
                    ret, frame = await asyncio.to_thread(cap.read)
                    if not ret:
                        logger.warning("RTSP read failed for %s, reconnecting", client_id)
                        break

                    sequence += 1
                    capture_ts = time.time()

                    # JPEG encode for storage (dashboard MJPEG still needs it)
                    _, jpeg_buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    jpeg_bytes = jpeg_buf.tobytes()

                    # Feed into session manager (auto-creates session if needed)
                    self._session_manager.update_frame(
                        client_id,
                        frame=frame,
                        jpeg_bytes=jpeg_bytes,
                        capture_timestamp=capture_ts,
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("RTSP consumer error for %s: %s", client_id, e)
            finally:
                if cap is not None:
                    await asyncio.to_thread(cap.release)

            if self._running:
                logger.info("RTSP reconnecting for %s in %.0fs", client_id, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    @staticmethod
    def _open_capture(rtsp_url: str) -> cv2.VideoCapture:
        """Open an RTSP stream with optimal settings for low latency."""
        # Try GStreamer backend first for lower latency
        build_info = cv2.getBuildInformation()
        has_gstreamer = "GStreamer" in build_info and "YES" in build_info

        if has_gstreamer:
            pipeline = (
                f'rtspsrc location={rtsp_url} latency=0 protocols=tcp '
                f'! rtph264depay ! h264parse ! avdec_h264 '
                f'! videoconvert ! video/x-raw,format=BGR '
                f'! appsink drop=true sync=false max-buffers=1'
            )
            cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if cap.isOpened():
                return cap
            logger.debug("GStreamer RTSP failed, falling back to FFmpeg")

        # Fallback: OpenCV FFmpeg backend
        cap = cv2.VideoCapture(rtsp_url)
        if cap.isOpened():
            # Reduce RTSP buffer for lower latency
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap
