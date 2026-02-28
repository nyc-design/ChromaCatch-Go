"""Manages the UxPlay AirPlay receiver process."""

import logging
import shutil
import subprocess
import threading
import time

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


class AirPlayManager:
    """Starts, monitors, and stops the UxPlay AirPlay mirroring receiver.

    UxPlay is launched with -vrtp to forward decrypted H.264 video as RTP
    packets over localhost UDP to the configured port.
    """

    def __init__(self, uxplay_path: str | None = None, udp_port: int | None = None, airplay_name: str | None = None):
        self.uxplay_path = uxplay_path or settings.uxplay_path
        self.udp_port = udp_port or settings.airplay_udp_port
        self.airplay_name = airplay_name or settings.airplay_name
        self._process: subprocess.Popen | None = None
        self._drain_threads: list[threading.Thread] = []


    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None


    def _check_uxplay_installed(self) -> bool:
        return shutil.which(self.uxplay_path) is not None


    def build_command(self) -> list[str]:
        """Build the UxPlay command with RTP forwarding.

        UxPlay automatically adds 'h264parse ! rtph264pay' before the -vrtp
        argument, so we only provide rtph264pay options and the UDP sink.
        """
        vrtp_pipeline = f"config-interval=1 ! udpsink host=127.0.0.1 port={self.udp_port}"
        return [self.uxplay_path, "-n", self.airplay_name, "-vrtp", vrtp_pipeline]


    def start(self) -> None:
        """Start the UxPlay process."""
        if self.is_running:
            logger.warning("UxPlay is already running (pid=%d)", self._process.pid)
            return

        if not self._check_uxplay_installed():
            raise RuntimeError(f"UxPlay not found at '{self.uxplay_path}'. " "Install it: https://github.com/FDH2/UxPlay")

        cmd = self.build_command()
        logger.info("Starting UxPlay: %s", " ".join(cmd))

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Give it a moment to initialize
        time.sleep(0.5)

        if not self.is_running:
            stderr = self._process.stderr.read().decode() if self._process.stderr else ""
            raise RuntimeError(f"UxPlay failed to start: {stderr}")

        # Drain stdout/stderr in background threads to prevent pipe buffer
        # from filling up and blocking UxPlay
        for stream, name in [(self._process.stdout, "stdout"), (self._process.stderr, "stderr")]:
            t = threading.Thread(target=self._drain_stream, args=(stream, name), daemon=True)
            t.start()
            self._drain_threads.append(t)

        logger.info("UxPlay started (pid=%d), forwarding to UDP port %d",
                     self._process.pid, self.udp_port)


    @staticmethod
    def _drain_stream(stream, name: str) -> None:
        """Read and discard a stream to prevent pipe buffer blocking."""
        try:
            while True:
                data = stream.read(4096)
                if not data:
                    break
        except Exception:
            pass
        logger.debug("UxPlay %s drain finished", name)

    def stop(self) -> None:
        """Stop the UxPlay process."""
        if not self.is_running:
            logger.debug("UxPlay is not running")
            return

        logger.info("Stopping UxPlay (pid=%d)", self._process.pid)
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("UxPlay did not terminate gracefully, killing")
            self._process.kill()
            self._process.wait(timeout=2)

        self._process = None
        self._drain_threads.clear()
        logger.info("UxPlay stopped")

    @property
    def pid(self) -> int | None:
        if self.is_running:
            return self._process.pid
        return None
