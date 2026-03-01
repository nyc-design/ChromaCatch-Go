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

    def test_gstreamer_pipe_preferred_over_cli(self):
        with patch("cv2.getBuildInformation", return_value="  GStreamer:                  NO"):
            with patch("shutil.which", return_value="/opt/homebrew/bin/gst-launch-1.0"):
                backend = FrameCapture._detect_backend()
                assert backend == CaptureBackend.GSTREAMER_PIPE

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
        (tmp_path / "frame_00010.jpg").write_bytes(b"a")
        (tmp_path / "frame_00002.jpg").write_bytes(b"a")
        (tmp_path / "frame_00001.jpg").write_bytes(b"a")
        files = capture._list_frame_files(str(tmp_path))
        assert [idx for idx, _ in files] == [1, 2, 10]

    def test_pick_next_frame_path_uses_first_when_frame_idx_none(self, capture, tmp_path: Path):
        (tmp_path / "frame_00004.jpg").write_bytes(b"a")
        (tmp_path / "frame_00009.jpg").write_bytes(b"a")
        path, idx = capture._pick_next_frame_path(str(tmp_path), None)
        assert idx == 4
        assert path.endswith("frame_00004.jpg")

    def test_pick_next_frame_path_jumps_forward_when_missing(self, capture, tmp_path: Path):
        (tmp_path / "frame_00105.jpg").write_bytes(b"a")
        (tmp_path / "frame_00108.jpg").write_bytes(b"a")
        path, idx = capture._pick_next_frame_path(str(tmp_path), 100)
        assert idx == 105
        assert path.endswith("frame_00105.jpg")

    def test_stop_when_not_started(self, capture):
        capture.stop()


class TestGStreamerPipeBackend:
    """Tests for the GSTREAMER_PIPE backend."""

    @pytest.fixture
    def pipe_capture(self):
        return FrameCapture(udp_port=5000, backend=CaptureBackend.GSTREAMER_PIPE)

    def test_pipe_backend_enum(self):
        assert CaptureBackend.GSTREAMER_PIPE.value == "gstreamer_pipe"

    def test_start_gst_pipe_process_builds_correct_command(self, pipe_capture):
        """Verify the pipe process command includes -q and fdsink."""
        with patch("shutil.which", return_value="/usr/bin/gst-launch-1.0"):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = mock_popen.return_value
                mock_proc.pid = 12345
                mock_proc.poll.return_value = None
                mock_proc.stderr = type("FakeStream", (), {"fileno": lambda s: 3})()
                mock_proc.stdout = type("FakeStream", (), {"fileno": lambda s: 4})()

                with patch("threading.Thread"):
                    with patch("select.select", return_value=([mock_proc.stdout], [], [])):
                        pipe_capture._running = True
                        proc = pipe_capture._start_gst_pipe_process()

                cmd = mock_popen.call_args[0][0]
                assert "-q" in cmd
                assert "fdsink" in cmd
                assert "fd=1" in cmd
                assert "jpegenc" in cmd
                assert "latency=20" in cmd

    def test_jpeg_boundary_parsing(self, pipe_capture):
        """Verify JPEG frame boundary detection from byte stream."""
        # Create two minimal valid JPEG frames
        import cv2
        frame1 = np.full((50, 50, 3), 100, dtype=np.uint8)
        frame2 = np.full((50, 50, 3), 200, dtype=np.uint8)
        _, jpg1 = cv2.imencode(".jpg", frame1, [cv2.IMWRITE_JPEG_QUALITY, 85])
        _, jpg2 = cv2.imencode(".jpg", frame2, [cv2.IMWRITE_JPEG_QUALITY, 85])

        # Verify SOI and EOI markers
        assert jpg1.tobytes()[:2] == b"\xff\xd8"
        assert jpg1.tobytes()[-2:] == b"\xff\xd9"

        # Simulate a byte stream with two JPEG frames concatenated
        stream = jpg1.tobytes() + jpg2.tobytes()
        buf = stream
        frames_found = []

        while True:
            soi = buf.find(b"\xff\xd8")
            if soi == -1:
                break
            if soi > 0:
                buf = buf[soi:]
                soi = 0
            eoi = buf.find(b"\xff\xd9", 2)
            if eoi == -1:
                break
            jpeg_data = buf[:eoi + 2]
            buf = buf[eoi + 2:]
            arr = np.frombuffer(jpeg_data, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                frames_found.append(frame)

        assert len(frames_found) == 2
        assert frames_found[0][25, 25, 0] == pytest.approx(100, abs=5)
        assert frames_found[1][25, 25, 0] == pytest.approx(200, abs=5)
