"""Tests for frame capture service."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from airplay_client.capture.frame_capture import CaptureBackend, FrameCapture


class TestCaptureBackend:
    def test_gstreamer_detection(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  YES (1.20.3)"):
            backend = FrameCapture._detect_backend()
            assert backend == CaptureBackend.GSTREAMER

    def test_gstreamer_cli_fallback(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  NO"):
            with patch("shutil.which", return_value="/opt/homebrew/bin/gst-launch-1.0"):
                backend = FrameCapture._detect_backend()
                assert backend == CaptureBackend.GSTREAMER_CLI

    def test_no_backend_raises(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  NO"):
            with patch("shutil.which", return_value=None):
                with pytest.raises(RuntimeError, match="No capture backend"):
                    FrameCapture._detect_backend()


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

    def test_push_frame_drops_oldest_when_full(self, capture):
        for i in range(5):
            capture._push_frame(np.full((10, 10, 3), i, dtype=np.uint8))
        assert capture.frame_queue.full()
        capture._push_frame(np.full((10, 10, 3), 99, dtype=np.uint8))
        frames = []
        while not capture.frame_queue.empty():
            frames.append(capture.frame_queue.get_nowait())
        assert frames[-1][0, 0, 0] == 99

    def test_infer_resolution_prefers_expected_shape(self, capture):
        # 1920x1080 and 540x3840 have the same byte size. We should prefer 1920x1080.
        frame_bytes = 1920 * 1080 * 3
        resolution = capture._infer_resolution_from_frame_size(frame_bytes)
        assert resolution == (1920, 1080)

    def test_infer_resolution_rejects_invalid_size(self, capture):
        assert capture._infer_resolution_from_frame_size(0) is None
        assert capture._infer_resolution_from_frame_size(7) is None

    def test_extract_resolution_from_caps_line(self, capture):
        line = (
            "video/x-raw, format=(string)BGR, width=(int)498, "
            "height=(int)1080, framerate=(fraction)0/1"
        )
        assert capture._extract_resolution_from_caps_line(line) == (498, 1080)

    def test_extract_resolution_from_caps_line_returns_none_without_dimensions(
        self,
        capture,
    ):
        assert capture._extract_resolution_from_caps_line("video/x-raw, format=BGR") is None

    def test_get_stable_file_size(self, capture, tmp_path: Path):
        f = tmp_path / "frame.raw"
        f.write_bytes(b"a" * 1234)
        size = capture._get_stable_file_size(str(f), timeout=0.5)
        assert size == 1234

    def test_list_frame_files_sorted(self, capture, tmp_path: Path):
        (tmp_path / "frame_00010.raw").write_bytes(b"a")
        (tmp_path / "frame_00002.raw").write_bytes(b"a")
        (tmp_path / "frame_00001.raw").write_bytes(b"a")
        files = capture._list_frame_files(str(tmp_path))
        assert [idx for idx, _ in files] == [1, 2, 10]

    def test_pick_next_frame_path_uses_first_when_frame_idx_none(self, capture, tmp_path: Path):
        (tmp_path / "frame_00004.raw").write_bytes(b"a")
        (tmp_path / "frame_00009.raw").write_bytes(b"a")
        path, idx = capture._pick_next_frame_path(str(tmp_path), None)
        assert idx == 4
        assert path.endswith("frame_00004.raw")

    def test_pick_next_frame_path_jumps_forward_when_missing(self, capture, tmp_path: Path):
        (tmp_path / "frame_00105.raw").write_bytes(b"a")
        (tmp_path / "frame_00108.raw").write_bytes(b"a")
        path, idx = capture._pick_next_frame_path(str(tmp_path), 100)
        assert idx == 105
        assert path.endswith("frame_00105.raw")

    def test_stop_when_not_started(self, capture):
        capture.stop()
