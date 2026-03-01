"""Tests for MediaMTX subprocess manager."""

import signal
from unittest.mock import MagicMock, patch

import pytest

from backend.mediamtx_manager import MediaMTXManager


class TestMediaMTXManager:
    @patch("backend.mediamtx_manager.settings")
    def test_disabled_by_default(self, mock_settings):
        mock_settings.mediamtx_enabled = False
        mock_settings.mediamtx_config = ""
        mgr = MediaMTXManager()
        mgr.start()
        assert not mgr.is_running

    @patch("backend.mediamtx_manager.settings")
    @patch("backend.mediamtx_manager.shutil.which", return_value=None)
    def test_missing_binary_warns(self, mock_which, mock_settings, caplog):
        mock_settings.mediamtx_enabled = True
        mock_settings.mediamtx_binary = "mediamtx"
        mock_settings.mediamtx_config = "/tmp/fake.yml"
        mgr = MediaMTXManager()
        mgr._config_path = "/tmp/fake.yml"
        import logging
        with caplog.at_level(logging.WARNING):
            mgr.start()
        assert not mgr.is_running
        assert "not found" in caplog.text

    def test_is_running_false_initially(self):
        mgr = MediaMTXManager.__new__(MediaMTXManager)
        mgr._proc = None
        mgr._config_path = "/tmp/fake.yml"
        assert not mgr.is_running

    def test_is_running_true_when_proc_alive(self):
        mgr = MediaMTXManager.__new__(MediaMTXManager)
        mgr._proc = MagicMock()
        mgr._proc.poll.return_value = None
        mgr._config_path = "/tmp/fake.yml"
        assert mgr.is_running

    def test_is_running_false_when_proc_exited(self):
        mgr = MediaMTXManager.__new__(MediaMTXManager)
        mgr._proc = MagicMock()
        mgr._proc.poll.return_value = 0
        mgr._config_path = "/tmp/fake.yml"
        assert not mgr.is_running

    def test_health_check_delegates(self):
        mgr = MediaMTXManager.__new__(MediaMTXManager)
        mgr._proc = MagicMock()
        mgr._proc.poll.return_value = None
        mgr._config_path = "/tmp/fake.yml"
        assert mgr.health_check() is True

    def test_stop_terminates_process(self):
        mgr = MediaMTXManager.__new__(MediaMTXManager)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0
        mgr._proc = mock_proc
        mgr._config_path = "/tmp/fake.yml"
        mgr.stop()
        mock_proc.send_signal.assert_called_once_with(signal.SIGTERM)
        assert mgr._proc is None

    def test_stop_kills_on_timeout(self):
        import subprocess
        mgr = MediaMTXManager.__new__(MediaMTXManager)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired("mediamtx", 5), 0]
        mgr._proc = mock_proc
        mgr._config_path = "/tmp/fake.yml"
        mgr.stop()
        mock_proc.kill.assert_called_once()

    def test_stop_noop_when_no_proc(self):
        mgr = MediaMTXManager.__new__(MediaMTXManager)
        mgr._proc = None
        mgr._config_path = "/tmp/fake.yml"
        mgr.stop()  # Should not raise
