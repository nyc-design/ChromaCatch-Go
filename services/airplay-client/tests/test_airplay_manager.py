"""Tests for AirPlay (UxPlay) process manager."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from airplay_client.capture.airplay_manager import AirPlayManager
from airplay_client.config import client_settings


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
        assert "-vs" not in cmd  # -vs 0 disables video, must not be present
        assert "-key" in cmd  # Persist server identity for reconnection
        key_idx = cmd.index("-key")
        assert cmd[key_idx + 1].endswith(".uxplay.pem")  # Explicit key path
        assert "-nohold" in cmd  # Drop stale connections
        assert "-reset" in cmd
        reset_idx = cmd.index("-reset")
        assert cmd[reset_idx + 1] == "0"  # Never auto-reset
        assert "-d" in cmd  # Debug logging
        vrtp_idx = cmd.index("-vrtp")
        pipeline = cmd[vrtp_idx + 1]
        assert "udpsink host=127.0.0.1 port=5000" in pipeline
        assert "config-interval=1" in pipeline
        assert "-artp" in cmd
        artp_idx = cmd.index("-artp")
        audio_pipeline = cmd[artp_idx + 1]
        assert f"port={manager.audio_udp_port}" in audio_pipeline
        # UxPlay auto-adds h264parse/rtph264pay, so they should NOT be in our pipeline
        assert "h264parse" not in pipeline
        assert "rtph264pay" not in pipeline

    def test_build_command_without_audio(self, manager):
        old_enabled = client_settings.audio_enabled
        old_source = client_settings.audio_source
        try:
            client_settings.audio_enabled = False
            cmd = manager.build_command()
            assert "-artp" not in cmd
        finally:
            client_settings.audio_enabled = old_enabled
            client_settings.audio_source = old_source

    def test_build_command_without_airplay_audio_mode(self, manager):
        old_enabled = client_settings.audio_enabled
        old_source = client_settings.audio_source
        try:
            client_settings.audio_enabled = True
            client_settings.audio_source = "system"
            cmd = manager.build_command()
            assert "-artp" not in cmd
        finally:
            client_settings.audio_enabled = old_enabled
            client_settings.audio_source = old_source

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
