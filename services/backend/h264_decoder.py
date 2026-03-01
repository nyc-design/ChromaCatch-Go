"""Streaming H.264 decoder using PyAV (FFmpeg wrapper).

Decodes individual H.264 Access Units received over WebSocket into
BGR numpy arrays for the CV pipeline and dashboard.
"""

import logging

import av
import numpy as np

logger = logging.getLogger(__name__)


class H264Decoder:
    """Stateful H.264 decoder that processes individual Access Units.

    Maintains a codec context across calls so that SPS/PPS state
    persists between keyframes and predicted frames.
    """

    def __init__(self) -> None:
        self._codec = av.CodecContext.create("h264", "r")
        self._frames_decoded = 0

    def decode(self, h264_au: bytes) -> np.ndarray | None:
        """Decode an H.264 Access Unit to a BGR numpy array.

        Args:
            h264_au: Raw H.264 Annex B bytes (one Access Unit).

        Returns:
            BGR numpy array, or None if the AU didn't produce a frame
            (e.g., SPS/PPS only, or decoder still buffering).
        """
        try:
            packet = av.Packet(h264_au)
            frames = self._codec.decode(packet)
            for frame in frames:
                bgr = frame.to_ndarray(format="bgr24")
                self._frames_decoded += 1
                return bgr
        except av.error.InvalidDataError as e:
            logger.warning("H.264 decode error (invalid data): %s", e)
        except Exception as e:
            logger.error("H.264 decode error: %s", e)
        return None

    def reset(self) -> None:
        """Reset the decoder state (e.g., after stream restart)."""
        self._codec = av.CodecContext.create("h264", "r")
        self._frames_decoded = 0
        logger.debug("H.264 decoder reset")

    @property
    def frames_decoded(self) -> int:
        return self._frames_decoded
