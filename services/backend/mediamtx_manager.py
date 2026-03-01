"""Manages the MediaMTX subprocess lifecycle alongside the backend.

MediaMTX is a media router that receives SRT streams from clients
and exposes them as RTSP (for CV pipeline) and WebRTC/WHEP (for dashboard).
"""

import asyncio
import logging
import os
import shutil
import signal
import subprocess

from backend.config import backend_settings as settings

logger = logging.getLogger(__name__)


class MediaMTXManager:
    """Manages MediaMTX subprocess lifecycle."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._config_path = self._resolve_config_path()

    @staticmethod
    def _resolve_config_path() -> str:
        """Find the mediamtx.yml configuration file."""
        if settings.mediamtx_config:
            return settings.mediamtx_config
        # Look relative to this file
        here = os.path.dirname(os.path.abspath(__file__))
        candidate = os.path.join(here, "mediamtx", "mediamtx.yml")
        if os.path.isfile(candidate):
            return candidate
        raise FileNotFoundError(
            f"mediamtx.yml not found. Set CC_BACKEND_MEDIAMTX_CONFIG or place it at {candidate}"
        )

    def start(self) -> None:
        """Start the MediaMTX subprocess."""
        if not settings.mediamtx_enabled:
            logger.info("MediaMTX is disabled (CC_BACKEND_MEDIAMTX_ENABLED=false)")
            return

        binary = shutil.which(settings.mediamtx_binary)
        if not binary:
            logger.warning(
                "MediaMTX binary %r not found in PATH. "
                "Install it: see scripts/install_mediamtx.sh",
                settings.mediamtx_binary,
            )
            return

        cmd = [binary, self._config_path]
        logger.info("Starting MediaMTX: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        logger.info("MediaMTX pid=%d, SRT=:%d, RTSP=:%d, WebRTC=:%d",
                     self._proc.pid,
                     settings.mediamtx_srt_port,
                     settings.mediamtx_rtsp_port,
                     settings.mediamtx_webrtc_port)

        # Start log drain thread
        import threading
        threading.Thread(target=self._drain_logs, daemon=True).start()

    def _drain_logs(self) -> None:
        """Read and forward MediaMTX logs."""
        if not self._proc or not self._proc.stdout:
            return
        try:
            for line in self._proc.stdout:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[mediamtx] %s", text)
        except Exception:
            pass

    def stop(self) -> None:
        """Stop the MediaMTX subprocess."""
        if self._proc and self._proc.poll() is None:
            logger.info("Stopping MediaMTX (pid=%d)", self._proc.pid)
            self._proc.send_signal(signal.SIGTERM)
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=3)
            logger.info("MediaMTX stopped")
        self._proc = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def health_check(self) -> bool:
        """Check if MediaMTX is running and responsive."""
        return self.is_running
