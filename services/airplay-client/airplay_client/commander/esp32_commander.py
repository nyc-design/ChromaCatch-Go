"""ESP32 commander — wraps ESP32Client in the Commander interface."""

from __future__ import annotations

import logging
import time

from airplay_client.commander.base import Commander, CommandResult
from airplay_client.commander.esp32_client import ESP32Client, HIDCommand

logger = logging.getLogger(__name__)


class ESP32Commander(Commander):
    """Routes game commands to an ESP32 via HTTP.

    Translates abstract commands into ESP32-specific HID commands
    (mouse move, click, swipe, press, release, key_press, key_release).
    """

    def __init__(self, esp32_client: ESP32Client | None = None) -> None:
        self._esp32 = esp32_client or ESP32Client()
        self._connected = False

    async def send_command(self, action: str, params: dict) -> CommandResult:
        forwarded_at = time.time()
        try:
            cmd = HIDCommand(action, **params)
            await self._esp32.send_command(cmd)
            return CommandResult(
                success=True,
                forwarded_at=forwarded_at,
                completed_at=time.time(),
            )
        except Exception as e:
            logger.error("ESP32 command failed (%s): %s", action, e)
            return CommandResult(
                success=False,
                forwarded_at=forwarded_at,
                completed_at=time.time(),
                error=str(e),
            )

    async def connect(self) -> None:
        self._connected = await self._esp32.ping()
        if self._connected:
            logger.info("ESP32 commander connected to %s:%d", self._esp32.host, self._esp32.port)
        else:
            logger.warning("ESP32 not reachable at %s:%d", self._esp32.host, self._esp32.port)

    async def disconnect(self) -> None:
        await self._esp32.close()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def commander_name(self) -> str:
        return "esp32"

    @property
    def supported_command_types(self) -> list[str]:
        return ["mouse", "keyboard"]
