"""Tests for JPEG frame encode/decode utilities."""

import numpy as np
import pytest

from shared.frame_codec import decode_frame, encode_frame


class TestEncodeFrame:
    def test_encode_returns_bytes(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        jpeg_bytes, w, h = encode_frame(frame, quality=70, max_dimension=0)
        assert isinstance(jpeg_bytes, bytes)
        assert len(jpeg_bytes) > 0
        assert w == 200
        assert h == 100

    def test_encode_resizes_when_too_large(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        jpeg_bytes, w, h = encode_frame(frame, quality=70, max_dimension=960)
        assert w == 960
        assert h == 540

    def test_encode_no_resize_when_small(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        jpeg_bytes, w, h = encode_frame(frame, quality=70, max_dimension=960)
        assert w == 640
        assert h == 480

    def test_encode_no_resize_when_disabled(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        jpeg_bytes, w, h = encode_frame(frame, quality=70, max_dimension=0)
        assert w == 1920
        assert h == 1080

    def test_encode_quality_affects_size(self):
        frame = np.random.randint(0, 255, (200, 300, 3), dtype=np.uint8)
        low_q, _, _ = encode_frame(frame, quality=10, max_dimension=0)
        high_q, _, _ = encode_frame(frame, quality=95, max_dimension=0)
        assert len(low_q) < len(high_q)

    def test_encode_preserves_aspect_ratio(self):
        frame = np.zeros((900, 1600, 3), dtype=np.uint8)
        _, w, h = encode_frame(frame, quality=70, max_dimension=800)
        assert w == 800
        assert h == 450

    def test_encode_tall_image(self):
        frame = np.zeros((1600, 900, 3), dtype=np.uint8)
        _, w, h = encode_frame(frame, quality=70, max_dimension=800)
        assert h == 800
        assert w == 450


class TestDecodeFrame:
    def test_decode_returns_numpy(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        jpeg_bytes, _, _ = encode_frame(frame, quality=90, max_dimension=0)
        decoded = decode_frame(jpeg_bytes)
        assert isinstance(decoded, np.ndarray)
        assert decoded.shape == (100, 200, 3)

    def test_decode_invalid_bytes_raises(self):
        with pytest.raises(RuntimeError, match="Failed to decode"):
            decode_frame(b"not a jpeg")

    def test_decode_empty_bytes_raises(self):
        with pytest.raises(RuntimeError, match="Failed to decode"):
            decode_frame(b"")


class TestRoundTrip:
    def test_encode_decode_preserves_shape(self):
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        jpeg_bytes, w, h = encode_frame(frame, quality=95, max_dimension=0)
        decoded = decode_frame(jpeg_bytes)
        assert decoded.shape == frame.shape

    def test_encode_decode_preserves_content_approximately(self):
        """JPEG is lossy but high quality should be close on smooth images."""
        # Use a smooth gradient (not random noise) since JPEG handles gradients well
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        for i in range(100):
            frame[i, :, :] = i * 2  # Smooth gradient
        jpeg_bytes, _, _ = encode_frame(frame, quality=100, max_dimension=0)
        decoded = decode_frame(jpeg_bytes)
        diff = np.abs(frame.astype(int) - decoded.astype(int))
        assert diff.mean() < 5

    def test_roundtrip_with_resize(self):
        frame = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
        jpeg_bytes, w, h = encode_frame(frame, quality=70, max_dimension=960)
        decoded = decode_frame(jpeg_bytes)
        assert decoded.shape == (540, 960, 3)
