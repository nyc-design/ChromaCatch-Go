"""Receives HID commands from the backend WebSocket and forwards to ESP32."""

import logging

from airplay_client.commander.esp32_client import ESP32Client, HIDCommand
from shared.messages import HIDCommandMessage

logger = logging.getLogger(__name__)


class ESP32Forwarder:
    """Translates HIDCommandMessage from backend into ESP32 HTTP calls."""

    def __init__(self, esp32_client: ESP32Client) -> None:
        self._esp32 = esp32_client

    async def handle_command(self, msg: HIDCommandMessage) -> None:
        """Forward a HID command to the ESP32."""
        try:
            cmd = HIDCommand(msg.action, **msg.params)
            result = await self._esp32.send_command(cmd)
            logger.debug("ESP32 executed %s: %s", msg.action, result)
        except Exception as e:
            logger.error("Failed to forward command to ESP32: %s", e)
