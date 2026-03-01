"""Tests for stale process cleanup helpers."""

from unittest.mock import MagicMock, patch

from airplay_client.capture.process_cleanup import _pgrep, cleanup_stale_airplay_processes


def test_pgrep_parses_pids():
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = "123\n456\n"
    with patch("subprocess.run", return_value=mock), patch("os.getpid", return_value=999):
        pids = _pgrep("dummy")
    assert pids == [123, 456]


def test_cleanup_no_matches_no_kill():
    mock = MagicMock()
    mock.returncode = 1
    mock.stdout = ""
    with (
        patch("subprocess.run", return_value=mock),
        patch("os.kill") as kill,
    ):
        cleanup_stale_airplay_processes(5000, 5002)
    kill.assert_not_called()
