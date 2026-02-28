"""System/capture-device audio source using ffmpeg."""

from __future__ import annotations

import logging
import platform
import queue
import shutil
import subprocess
import threading

from airplay_client.audio.base import AudioSource
from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


class FFmpegAudioSource(AudioSource):
    """Capture low-latency PCM audio from local system/device via ffmpeg."""

    def __init__(
        self,
        sample_rate: int | None = None,
        channels: int | None = None,
        chunk_ms: int | None = None,
        input_backend: str | None = None,
        input_device: str | None = None,
    ) -> None:
        self._sample_rate = sample_rate or settings.audio_sample_rate
        self._channels = channels or settings.audio_channels
        self._chunk_ms = chunk_ms or settings.audio_chunk_ms
        self._input_backend = (input_backend or settings.audio_input_backend).strip()
        self._input_device = (input_device or settings.audio_input_device).strip()

        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=20)
        self._thread: threading.Thread | None = None
        self._running = False
        self._ffmpeg_proc: subprocess.Popen | None = None

    @property
    def source_name(self) -> str:
        return "system"

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @staticmethod
    def _detect_default_backend() -> str:
        system = platform.system().lower()
        if system == "darwin":
            return "avfoundation"
        if system == "linux":
            return "pulse"
        if system == "windows":
            return "dshow"
        raise RuntimeError(f"Unsupported platform for system audio capture: {system}")

    def _resolve_backend(self) -> str:
        backend = self._input_backend.lower() if self._input_backend else "auto"
        if backend == "auto":
            backend = self._detect_default_backend()
        if backend not in {"avfoundation", "pulse", "dshow"}:
            raise RuntimeError(
                "Unsupported audio input backend "
                f"'{self._input_backend}'. Use auto|avfoundation|pulse|dshow."
            )
        return backend

    @staticmethod
    def _default_device_for_backend(backend: str) -> str:
        if backend == "avfoundation":
            # avfoundation uses "<video>:<audio>" input selector; blank video, device 0.
            return ":0"
        if backend == "pulse":
            return "default"
        # dshow requires explicit device names in most environments.
        return ""

    def _resolve_input(self) -> tuple[str, str]:
        backend = self._resolve_backend()
        device = self._input_device or self._default_device_for_backend(backend)
        if backend == "dshow" and not device:
            raise RuntimeError(
                "audio_input_device is required when audio_input_backend=dshow"
            )
        return backend, device

    def _build_command(self) -> list[str]:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg not found (required for system audio capture)")

        backend, device = self._resolve_input()
        chunk_size = max(1, int((self._sample_rate * self._channels * 2) * (self._chunk_ms / 1000.0)))

        input_selector = device
        if backend == "dshow":
            input_selector = f"audio={device}"

        # Low-latency ffmpeg capture to raw PCM on stdout.
        cmd = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-f",
            backend,
            "-i",
            input_selector,
            "-ac",
            str(self._channels),
            "-ar",
            str(self._sample_rate),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "pipe:1",
        ]
        logger.debug(
            "System audio ffmpeg backend=%s, device=%s, chunk_bytes=%d",
            backend,
            device or "<unset>",
            chunk_size,
        )
        return cmd

    def _push_chunk(self, chunk: bytes) -> None:
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
        self._queue.put(chunk)

    def _capture_loop(self) -> None:
        bytes_per_chunk = max(
            2,
            int((self._sample_rate * self._channels * 2) * (self._chunk_ms / 1000.0)),
        )
        cmd = self._build_command()
        logger.info("Starting system audio capture: %s", " ".join(cmd))

        self._ffmpeg_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        assert self._ffmpeg_proc.stdout is not None
        aggregate = bytearray()

        while self._running:
            if self._ffmpeg_proc.poll() is not None:
                logger.warning(
                    "System audio ffmpeg exited (rc=%s)", self._ffmpeg_proc.returncode
                )
                break
            chunk = self._ffmpeg_proc.stdout.read(bytes_per_chunk)
            if not chunk:
                continue
            aggregate.extend(chunk)
            while len(aggregate) >= bytes_per_chunk:
                self._push_chunk(bytes(aggregate[:bytes_per_chunk]))
                del aggregate[:bytes_per_chunk]

        if aggregate:
            self._push_chunk(bytes(aggregate))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("System audio capture started")

    def stop(self) -> None:
        self._running = False
        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
            self._ffmpeg_proc.kill()
            self._ffmpeg_proc.wait(timeout=2)
        self._ffmpeg_proc = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("System audio capture stopped")

    def get_chunk(self, timeout: float = 0.5) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
