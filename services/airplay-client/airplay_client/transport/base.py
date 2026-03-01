"""Abstract base class for media transport."""

from abc import ABC, abstractmethod


class MediaTransport(ABC):
    """Base class for media transport backends (SRT or WebSocket).

    A MediaTransport is responsible for delivering video and audio data
    from the client to the backend. The control plane (commands, status,
    ACKs) always uses WebSocket regardless of transport mode.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the transport (connect / launch subprocess)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the transport and clean up resources."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is actively delivering media."""

    @property
    @abstractmethod
    def transport_name(self) -> str:
        """Human-readable transport name for status reporting."""
