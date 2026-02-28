"""Tests for frame capture service."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from airplay_client.capture.frame_capture import CaptureBackend, FrameCapture


class TestCaptureBackend:
    def test_gstreamer_detection(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  YES (1.20.3)"):
            backend = FrameCapture._detect_backend()
            assert backend == CaptureBackend.GSTREAMER

    def test_ffmpeg_fallback(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  NO"):
            backend = FrameCapture._detect_backend()
            assert backend == CaptureBackend.FFMPEG


class TestFrameCapture:
    @pytest.fixture
    def capture(self):
        return FrameCapture(udp_port=5000, backend=CaptureBackend.GSTREAMER)

    def test_gstreamer_pipeline_string(self, capture):
        pipeline = capture._build_gstreamer_pipeline()
        assert "udpsrc port=5000" in pipeline
        assert "rtph264depay" in pipeline
        assert "appsink" in pipeline

    def test_not_running_initially(self, capture):
        assert capture.is_running is False

    def test_get_frame_returns_none_when_empty(self, capture):
        assert capture.get_frame(timeout=0.01) is None

    def test_frame_queue_operations(self, capture):
        test_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        capture.frame_queue.put(test_frame)
        result = capture.get_frame(timeout=0.1)
        assert result is not None
        assert result.shape == (100, 100, 3)

    def test_frame_queue_drops_old_when_full(self, capture):
        for i in range(5):
            frame = np.full((10, 10, 3), i, dtype=np.uint8)
            capture.frame_queue.put(frame)

        assert capture.frame_queue.full()
        capture.frame_queue.get_nowait()
        new_frame = np.full((10, 10, 3), 99, dtype=np.uint8)
        capture.frame_queue.put(new_frame)

        frames = []
        while not capture.frame_queue.empty():
            frames.append(capture.frame_queue.get_nowait())
        assert frames[0][0, 0, 0] == 1
        assert frames[-1][0, 0, 0] == 99

    @patch("cv2.VideoCapture")
    def test_create_capture_gstreamer(self, mock_cv2_cap, capture):
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = True
        mock_cv2_cap.return_value = mock_cap
        cap = capture._create_capture()
        assert cap.isOpened()

    @patch("cv2.VideoCapture")
    def test_create_capture_fails_if_not_opened(self, mock_cv2_cap, capture):
        mock_cap = MagicMock()
        mock_cap.isOpened.return_value = False
        mock_cv2_cap.return_value = mock_cap
        with pytest.raises(RuntimeError, match="Failed to open video capture"):
            capture._create_capture()

    def test_stop_when_not_started(self, capture):
        capture.stop()
