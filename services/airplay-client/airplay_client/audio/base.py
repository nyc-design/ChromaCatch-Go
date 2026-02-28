"""Base interface for audio chunk producers."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AudioSource(ABC):
    """Common interface for any audio chunk producer."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Human-readable source type."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Audio sample rate in Hz."""

    @property
    @abstractmethod
    def channels(self) -> int:
        """Audio channel count."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """Whether source is actively producing audio chunks."""

    @abstractmethod
    def start(self) -> None:
        """Start audio source."""

    @abstractmethod
    def stop(self) -> None:
        """Stop audio source."""

    @abstractmethod
    def get_chunk(self, timeout: float = 0.5) -> bytes | None:
        """Get latest PCM chunk, blocking up to timeout seconds."""
