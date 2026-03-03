"""Abstract base class for command output targets."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time


@dataclass
class CommandResult:
    """Result of executing a command on a target device."""

    success: bool
    received_at: float = field(default_factory=time.time)
    forwarded_at: float | None = None
    completed_at: float = field(default_factory=time.time)
    error: str | None = None


class Commander(ABC):
    """Base class for all command output targets.

    A Commander translates abstract game commands into target-specific
    input actions (ESP32 HID, sys-botbase TCP, Luma3DS UDP, virtual
    gamepad, etc.).
    """

    @abstractmethod
    async def send_command(self, action: str, params: dict) -> CommandResult:
        """Send a command to the target device.

        Args:
            action: The command action (e.g. "move", "click", "button_press",
                    "stick", "tap", "key_press").
            params: Action-specific parameters (e.g. {"dx": 10, "dy": -5}
                    for mouse move, {"button": "A"} for gamepad).

        Returns:
            CommandResult with timing and success info.
        """

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the target device."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the target device."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the commander has an active connection to the target."""

    @property
    @abstractmethod
    def commander_name(self) -> str:
        """Human-readable commander name for status reporting."""

    @property
    @abstractmethod
    def supported_command_types(self) -> list[str]:
        """List of supported command types (e.g. ['mouse', 'keyboard'])."""
