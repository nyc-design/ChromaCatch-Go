"""Factory for creating the appropriate commander."""

import logging

from airplay_client.commander.base import Commander
from airplay_client.config import client_settings

logger = logging.getLogger(__name__)


def create_commander() -> Commander:
    """Create a commander based on config.

    Reads CC_CLIENT_COMMANDER_MODE to determine the target.

    Returns:
        A Commander instance for the configured target.
    """
    mode = client_settings.commander_mode.lower()

    if mode == "esp32":
        from airplay_client.commander.esp32_commander import ESP32Commander
        logger.info("Using ESP32 commander (BLE HID via HTTP)")
        return ESP32Commander()
    elif mode == "sysbotbase":
        from airplay_client.commander.sysbotbase_client import SysBotbaseCommander
        logger.info("Using sys-botbase commander (Switch TCP)")
        return SysBotbaseCommander()
    elif mode == "luma3ds":
        from airplay_client.commander.luma3ds_client import Luma3DSCommander
        logger.info("Using Luma3DS input redirect commander (3DS UDP)")
        return Luma3DSCommander()
    elif mode == "virtual-gamepad":
        from airplay_client.commander.virtual_gamepad import VirtualGamepadCommander
        logger.info("Using virtual gamepad commander (OS-level)")
        return VirtualGamepadCommander()
    else:
        raise ValueError(
            f"Unknown commander mode: {mode!r}. "
            "Use 'esp32', 'sysbotbase', 'luma3ds', or 'virtual-gamepad'."
        )
