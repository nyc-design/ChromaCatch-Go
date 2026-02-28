"""JPEG encode/decode utilities for frame transport."""

import cv2
import numpy as np


def encode_frame(frame: np.ndarray, quality: int = 70, max_dimension: int = 960) -> tuple[bytes, int, int]:
    """Encode a BGR numpy frame to JPEG bytes, with optional resize.

    Args:
        frame: BGR numpy array (H, W, 3)
        quality: JPEG quality 1-100
        max_dimension: Resize so longest edge <= this value. 0 = no resize.

    Returns:
        (jpeg_bytes, final_width, final_height)
    """
    h, w = frame.shape[:2]

    if max_dimension > 0 and max(h, w) > max_dimension:
        scale = max_dimension / max(h, w)
        new_w = int(w * scale)
        new_h = int(h * scale)
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
        h, w = new_h, new_w

    encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
    success, jpeg_buf = cv2.imencode(".jpg", frame, encode_params)
    if not success:
        raise RuntimeError("Failed to encode frame as JPEG")

    return jpeg_buf.tobytes(), w, h


def decode_frame(jpeg_bytes: bytes) -> np.ndarray:
    """Decode JPEG bytes back to a BGR numpy array."""
    if not jpeg_bytes:
        raise RuntimeError("Failed to decode JPEG frame: empty input")
    buf = np.frombuffer(jpeg_bytes, dtype=np.uint8)
    frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if frame is None:
        raise RuntimeError("Failed to decode JPEG frame")
    return frame
