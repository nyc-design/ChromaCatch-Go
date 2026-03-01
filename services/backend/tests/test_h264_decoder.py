"""Tests for H.264 decoder (PyAV-based)."""

from fractions import Fraction

import numpy as np
import pytest

from backend.h264_decoder import H264Decoder


def _make_h264_keyframe() -> bytes:
    """Generate a minimal valid H.264 keyframe (SPS + PPS + IDR) using PyAV.

    Creates a 64x64 black frame encoded as H.264 Annex B byte stream.
    """
    import av

    codec = av.CodecContext.create("h264", "w")
    codec.width = 64
    codec.height = 64
    codec.pix_fmt = "yuv420p"
    codec.time_base = Fraction(1, 30)
    codec.options = {"preset": "ultrafast", "tune": "zerolatency"}
    codec.open()

    frame = av.VideoFrame(64, 64, "yuv420p")
    # Fill with black (Y=0, U=128, V=128)
    for i, plane in enumerate(frame.planes):
        if i == 0:
            plane.update(bytes(plane.buffer_size))
        else:
            plane.update(bytes([128]) * plane.buffer_size)
    frame.pts = 0

    packets = codec.encode(frame)
    # Flush encoder
    packets += codec.encode(None)

    # Concatenate all packet data
    h264_data = b""
    for pkt in packets:
        h264_data += bytes(pkt)
    return h264_data


class TestH264Decoder:
    def test_create_decoder(self):
        decoder = H264Decoder()
        assert decoder.frames_decoded == 0

    def test_decode_returns_none_for_empty_data(self):
        decoder = H264Decoder()
        result = decoder.decode(b"")
        assert result is None

    def test_decode_returns_none_for_garbage(self):
        decoder = H264Decoder()
        result = decoder.decode(b"\x00\x01\x02\x03\x04\x05")
        assert result is None

    def test_decode_valid_h264(self):
        h264_data = _make_h264_keyframe()
        decoder = H264Decoder()
        frame = decoder.decode(h264_data)
        assert frame is not None
        assert isinstance(frame, np.ndarray)
        assert frame.ndim == 3
        assert frame.shape[2] == 3  # BGR
        assert frame.shape[0] == 64
        assert frame.shape[1] == 64

    def test_frames_decoded_counter(self):
        h264_data = _make_h264_keyframe()
        decoder = H264Decoder()
        decoder.decode(h264_data)
        assert decoder.frames_decoded >= 1

    def test_reset_clears_state(self):
        h264_data = _make_h264_keyframe()
        decoder = H264Decoder()
        decoder.decode(h264_data)
        assert decoder.frames_decoded >= 1
        decoder.reset()
        assert decoder.frames_decoded == 0
