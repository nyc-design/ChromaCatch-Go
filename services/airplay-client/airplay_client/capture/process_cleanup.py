"""Helpers for cleaning up stale local media processes."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time

logger = logging.getLogger(__name__)


def _pgrep(pattern: str) -> list[int]:
    try:
        proc = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []

    if proc.returncode not in (0, 1):
        return []
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != os.getpid():
            pids.append(pid)
    return pids


def _kill_pids(pids: list[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            continue


def cleanup_stale_airplay_processes(video_port: int, audio_port: int) -> None:
    """Kill stale UxPlay/GStreamer processes from previous crashed runs."""
    patterns = [
        r"uxplay .*udpsink host=127\\.0\\.0\\.1 port={}".format(video_port),
        r"gst-launch-1\\.0 .*port={}".format(video_port),
        r"gst-launch-1\\.0 .*port={}".format(audio_port),
    ]
    stale: list[int] = []
    for pattern in patterns:
        stale.extend(_pgrep(pattern))

    stale = sorted(set(stale))
    if not stale:
        return

    logger.warning("Cleaning up stale media processes: %s", stale)
    _kill_pids(stale, signal.SIGTERM)
    time.sleep(0.5)
    remaining = [pid for pid in stale if os.path.exists(f"/proc/{pid}") or _pid_alive(pid)]
    if remaining:
        _kill_pids(remaining, signal.SIGKILL)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
