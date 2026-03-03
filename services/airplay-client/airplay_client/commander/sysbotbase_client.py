"""sys-botbase commander — sends controller input to a modded Nintendo Switch.

Connects to sys-botbase sysmodule over TCP port 6000. Commands are plain-text
strings terminated by newlines (e.g. "click A\\n", "setStick LEFT 0 32767\\n").

Requires: Atmosphere CFW + sys-botbase sysmodule running on the Switch.
"""

from __future__ import annotations

import asyncio
import logging
import time

from airplay_client.commander.base import Commander, CommandResult
from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)

# sys-botbase button name mapping (GameCommand button → sys-botbase name)
_BUTTON_MAP = {
    "a": "A", "b": "B", "x": "X", "y": "Y",
    "l": "L", "r": "R", "zl": "ZL", "zr": "ZR",
    "plus": "PLUS", "minus": "MINUS", "start": "PLUS", "select": "MINUS",
    "lstick": "LSTICK", "rstick": "RSTICK",
    "home": "HOME", "capture": "CAPTURE",
    "dup": "DUP", "ddown": "DDOWN", "dleft": "DLEFT", "dright": "DRIGHT",
    "up": "DUP", "down": "DDOWN", "left": "DLEFT", "right": "DRIGHT",
}


class SysBotbaseCommander(Commander):
    """Routes game commands to a modded Switch via sys-botbase TCP."""

    def __init__(self, host: str | None = None, port: int | None = None) -> None:
        self._host = host or settings.commander_host or "192.168.1.50"
        self._port = port or settings.commander_port or 6000
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False

    async def send_command(self, action: str, params: dict) -> CommandResult:
        if not self._connected:
            await self.connect()

        forwarded_at = time.time()
        try:
            text_cmd = self._translate(action, params)
            if text_cmd is None:
                return CommandResult(success=False, forwarded_at=forwarded_at, completed_at=time.time(), error=f"unsupported action: {action}")

            self._writer.write(f"{text_cmd}\r\n".encode())
            await self._writer.drain()

            # sys-botbase doesn't send responses for most commands
            logger.debug("sys-botbase: %s", text_cmd)
            return CommandResult(success=True, forwarded_at=forwarded_at, completed_at=time.time())
        except Exception as e:
            self._connected = False
            logger.error("sys-botbase command failed: %s", e)
            return CommandResult(success=False, forwarded_at=forwarded_at, completed_at=time.time(), error=str(e))

    def _translate(self, action: str, params: dict) -> str | None:
        """Translate a GameCommand action+params to sys-botbase text."""
        if action == "button_press":
            btn = _BUTTON_MAP.get(str(params.get("button", "")).lower())
            if btn:
                return f"click {btn}"
        elif action == "button_hold":
            btn = _BUTTON_MAP.get(str(params.get("button", "")).lower())
            if btn:
                return f"press {btn}"
        elif action == "button_release":
            btn = _BUTTON_MAP.get(str(params.get("button", "")).lower())
            if btn:
                return f"release {btn}"
        elif action == "stick":
            stick = "LEFT" if params.get("stick_id", "left") == "left" else "RIGHT"
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            return f"setStick {stick} {x} {y}"
        elif action == "tap":
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            return f"touch {x} {y}"
        elif action == "touch_hold":
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            duration = int(params.get("duration_ms", 100))
            return f"touchHold {x} {y} {duration}"
        return None

    async def connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=5.0
            )
            self._connected = True
            logger.info("sys-botbase connected to %s:%d", self._host, self._port)
        except Exception as e:
            self._connected = False
            logger.error("sys-botbase connection failed (%s:%d): %s", self._host, self._port, e)

    async def disconnect(self) -> None:
        if self._writer:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def commander_name(self) -> str:
        return "sysbotbase"

    @property
    def supported_command_types(self) -> list[str]:
        return ["gamepad", "touch"]
