"""AirPlay RTP-backed audio source."""

from __future__ import annotations

from airplay_client.audio.base import AudioSource
from airplay_client.capture.audio_capture import AudioCapture


class AirPlayAudioSource(AudioSource):
    """Audio source that consumes UxPlay RTP audio forwarding."""

    def __init__(self) -> None:
        self._capture = AudioCapture()

    @property
    def source_name(self) -> str:
        return "airplay"

    @property
    def sample_rate(self) -> int:
        return self._capture.sample_rate

    @property
    def channels(self) -> int:
        return self._capture.channels

    @property
    def is_running(self) -> bool:
        return self._capture.is_running

    def start(self) -> None:
        self._capture.start()

    def stop(self) -> None:
        self._capture.stop()

    def get_chunk(self, timeout: float = 0.5) -> bytes | None:
        return self._capture.get_chunk(timeout=timeout)
