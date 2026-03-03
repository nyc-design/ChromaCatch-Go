"""Luma3DS input redirect commander — sends controller input to a modded 3DS.

Sends 20-byte UDP packets to port 4950 on the 3DS running Luma3DS's Rosalina
input redirection. Packet format: HID buttons (u32) + touchscreen (u32) +
circle pad (u32) + c-stick/IR (u32) + special buttons (u32).

Requires: Luma3DS CFW with Rosalina input redirection enabled on the 3DS.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time

from airplay_client.commander.base import Commander, CommandResult
from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)

# 3DS HID button bitmask (active LOW — 0 = pressed)
_BUTTON_BITS = {
    "a": 0, "b": 1, "select": 2, "start": 3,
    "dright": 4, "dleft": 5, "dup": 6, "ddown": 7,
    "r": 8, "l": 9, "x": 10, "y": 11,
    "right": 4, "left": 5, "up": 6, "down": 7,
    "zl": 14, "zr": 15,  # New 3DS only
}

# Default state: all buttons released (all bits set = not pressed)
_DEFAULT_BUTTONS = 0xFFF
_DEFAULT_TOUCH = 0x02000000  # Touch not active
_DEFAULT_CIRCLE_PAD = 0x00800080  # Centered (128, 128)
_DEFAULT_CSTICK = 0x80800081  # Centered (128, 128) + default button byte
_DEFAULT_SPECIAL = 0x00000000  # No special buttons


class Luma3DSCommander(Commander):
    """Routes game commands to a 3DS via Luma3DS UDP input redirect."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self._host = host or settings.commander_host or "192.168.1.60"
        self._port = port or settings.commander_port or 4950
        self._sock: socket.socket | None = None
        self._connected = False
        # Current input state (maintained between calls)
        self._buttons = _DEFAULT_BUTTONS
        self._touch = _DEFAULT_TOUCH
        self._circle_pad = _DEFAULT_CIRCLE_PAD
        self._cstick = _DEFAULT_CSTICK
        self._special = _DEFAULT_SPECIAL

    async def send_command(self, action: str, params: dict) -> CommandResult:
        if not self._connected:
            await self.connect()

        forwarded_at = time.time()
        try:
            self._update_state(action, params)
            packet = struct.pack("<IIIII", self._buttons, self._touch, self._circle_pad, self._cstick, self._special)
            await asyncio.get_event_loop().run_in_executor(None, self._sock.sendto, packet, (self._host, self._port))
            logger.debug("luma3ds: %s %s → %s", action, params, packet.hex())
            return CommandResult(success=True, forwarded_at=forwarded_at, completed_at=time.time())
        except Exception as e:
            logger.error("Luma3DS command failed: %s", e)
            return CommandResult(success=False, forwarded_at=forwarded_at, completed_at=time.time(), error=str(e))

    def _update_state(self, action: str, params: dict) -> None:
        """Update internal input state based on action."""
        if action == "button_press":
            bit = _BUTTON_BITS.get(str(params.get("button", "")).lower())
            if bit is not None:
                self._buttons &= ~(1 << bit)  # Active LOW: clear bit = pressed
        elif action == "button_release":
            bit = _BUTTON_BITS.get(str(params.get("button", "")).lower())
            if bit is not None:
                self._buttons |= (1 << bit)  # Set bit = released
        elif action == "stick":
            # Circle pad: signed values mapped to 0-255 (128 = center)
            x = int(params.get("x", 0))  # -128 to 127
            y = int(params.get("y", 0))  # -128 to 127
            cx = max(0, min(255, x + 128))
            cy = max(0, min(255, y + 128))
            self._circle_pad = (cy << 8) | cx
        elif action == "tap":
            # Touch screen: 12-bit X (0-319) + 12-bit Y (0-239)
            x = max(0, min(319, int(params.get("x", 0))))
            y = max(0, min(239, int(params.get("y", 0))))
            self._touch = (1 << 24) | (y << 12) | x  # bit 24 = touch active
        elif action == "touch_release":
            self._touch = _DEFAULT_TOUCH
        elif action == "reset":
            self._buttons = _DEFAULT_BUTTONS
            self._touch = _DEFAULT_TOUCH
            self._circle_pad = _DEFAULT_CIRCLE_PAD
            self._cstick = _DEFAULT_CSTICK
            self._special = _DEFAULT_SPECIAL

    async def connect(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._connected = True
            logger.info("Luma3DS commander ready → %s:%d", self._host, self._port)
        except Exception as e:
            self._connected = False
            logger.error("Luma3DS socket creation failed: %s", e)

    async def disconnect(self) -> None:
        if self._sock:
            self._sock.close()
        self._sock = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def commander_name(self) -> str:
        return "luma3ds"

    @property
    def supported_command_types(self) -> list[str]:
        return ["gamepad", "touch"]
