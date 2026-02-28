"""Tests for AirPlay (UxPlay) process manager."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from airplay_client.capture.airplay_manager import AirPlayManager


class TestAirPlayManager:
    @pytest.fixture
    def manager(self):
        return AirPlayManager(
            uxplay_path="uxplay",
            udp_port=5000,
            airplay_name="TestCatch",
        )

    def test_build_command(self, manager):
        cmd = manager.build_command()
        assert cmd[0] == "uxplay"
        assert "-n" in cmd
        assert "TestCatch" in cmd
        assert "-vrtp" in cmd
        assert "-vs" in cmd
        assert "0" in cmd
        vrtp_idx = cmd.index("-vrtp")
        pipeline = cmd[vrtp_idx + 1]
        assert "udpsink host=127.0.0.1 port=5000" in pipeline

    def test_not_running_initially(self, manager):
        assert manager.is_running is False
        assert manager.pid is None

    def test_stop_when_not_running(self, manager):
        manager.stop()

    @patch("shutil.which", return_value=None)
    def test_start_fails_if_uxplay_not_installed(self, mock_which, manager):
        with pytest.raises(RuntimeError, match="UxPlay not found"):
            manager.start()

    @patch("shutil.which", return_value="/usr/bin/uxplay")
    @patch("subprocess.Popen")
    def test_start_success(self, mock_popen, mock_which, manager):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        with patch("time.sleep"):
            manager.start()

        assert manager.is_running is True
        assert manager.pid == 12345

    @patch("shutil.which", return_value="/usr/bin/uxplay")
    @patch("subprocess.Popen")
    def test_start_fails_if_process_exits(self, mock_popen, mock_which, manager):
        mock_process = MagicMock()
        mock_process.poll.return_value = 1
        mock_process.stderr.read.return_value = b"some error"
        mock_popen.return_value = mock_process

        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="UxPlay failed to start"):
                manager.start()

    @patch("shutil.which", return_value="/usr/bin/uxplay")
    @patch("subprocess.Popen")
    def test_stop_terminates_process(self, mock_popen, mock_which, manager):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        with patch("time.sleep"):
            manager.start()

        manager.stop()
        mock_process.terminate.assert_called_once()
        assert manager.is_running is False

    @patch("shutil.which", return_value="/usr/bin/uxplay")
    @patch("subprocess.Popen")
    def test_start_warns_if_already_running(self, mock_popen, mock_which, manager):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        with patch("time.sleep"):
            manager.start()
            manager.start()

        assert mock_popen.call_count == 1

    @patch("shutil.which", return_value="/usr/bin/uxplay")
    @patch("subprocess.Popen")
    def test_stop_kills_if_terminate_times_out(self, mock_popen, mock_which, manager):
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 12345
        mock_process.wait.side_effect = [subprocess.TimeoutExpired("uxplay", 5), None]
        mock_popen.return_value = mock_process

        with patch("time.sleep"):
            manager.start()

        manager.stop()
        mock_process.terminate.assert_called_once()
        mock_process.kill.assert_called_once()
