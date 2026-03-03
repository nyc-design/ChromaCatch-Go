"""Tests for stub commanders (sysbotbase, luma3ds, virtual-gamepad)."""

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from airplay_client.commander.sysbotbase_client import SysBotbaseCommander
from airplay_client.commander.luma3ds_client import Luma3DSCommander
from airplay_client.commander.virtual_gamepad import VirtualGamepadCommander


# --- SysBotbaseCommander ---


class TestSysBotbaseCommander:
    @pytest.fixture
    def commander(self):
        return SysBotbaseCommander(host="192.168.1.50", port=6000)

    def test_commander_name(self, commander):
        assert commander.commander_name == "sysbotbase"

    def test_supported_command_types(self, commander):
        assert "gamepad" in commander.supported_command_types
        assert "touch" in commander.supported_command_types

    def test_not_connected_initially(self, commander):
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_success(self):
        commander = SysBotbaseCommander(host="127.0.0.1", port=6000)
        with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_conn:
            mock_reader = MagicMock()
            mock_writer = MagicMock()
            mock_writer.close = MagicMock()
            mock_writer.wait_closed = AsyncMock()
            mock_conn.return_value = (mock_reader, mock_writer)
            await commander.connect()
            assert commander.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        commander = SysBotbaseCommander(host="unreachable", port=6000)
        with patch("asyncio.open_connection", new_callable=AsyncMock, side_effect=OSError("refused")):
            # wait_for wraps open_connection
            with patch("asyncio.wait_for", new_callable=AsyncMock, side_effect=OSError("refused")):
                await commander.connect()
                assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_translate_button_press(self, commander):
        result = commander._translate("button_press", {"button": "A"})
        assert result == "click A"

    @pytest.mark.asyncio
    async def test_translate_button_hold(self, commander):
        result = commander._translate("button_hold", {"button": "B"})
        assert result == "press B"

    @pytest.mark.asyncio
    async def test_translate_button_release(self, commander):
        result = commander._translate("button_release", {"button": "ZL"})
        assert result == "release ZL"

    @pytest.mark.asyncio
    async def test_translate_stick(self, commander):
        result = commander._translate("stick", {"stick_id": "left", "x": 32767, "y": 0})
        assert result == "setStick LEFT 32767 0"

    @pytest.mark.asyncio
    async def test_translate_stick_right(self, commander):
        result = commander._translate("stick", {"stick_id": "right", "x": -32768, "y": 32767})
        assert result == "setStick RIGHT -32768 32767"

    @pytest.mark.asyncio
    async def test_translate_tap(self, commander):
        result = commander._translate("tap", {"x": 640, "y": 360})
        assert result == "touch 640 360"

    @pytest.mark.asyncio
    async def test_translate_touch_hold(self, commander):
        result = commander._translate("touch_hold", {"x": 100, "y": 200, "duration_ms": 500})
        assert result == "touchHold 100 200 500"

    @pytest.mark.asyncio
    async def test_translate_dpad(self, commander):
        # "up" maps to "DUP"
        result = commander._translate("button_press", {"button": "up"})
        assert result == "click DUP"

    @pytest.mark.asyncio
    async def test_translate_unknown_returns_none(self, commander):
        result = commander._translate("unknown_action", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_disconnect(self, commander):
        await commander.disconnect()
        assert commander.is_connected is False


# --- Luma3DSCommander ---


class TestLuma3DSCommander:
    @pytest.fixture
    def commander(self):
        return Luma3DSCommander(host="192.168.1.60", port=4950)

    def test_commander_name(self, commander):
        assert commander.commander_name == "luma3ds"

    def test_supported_command_types(self, commander):
        assert "gamepad" in commander.supported_command_types
        assert "touch" in commander.supported_command_types

    def test_not_connected_initially(self, commander):
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_creates_socket(self, commander):
        await commander.connect()
        assert commander.is_connected is True
        assert commander._sock is not None
        await commander.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_closes_socket(self, commander):
        await commander.connect()
        await commander.disconnect()
        assert commander.is_connected is False
        assert commander._sock is None

    def test_button_press_updates_state(self, commander):
        # Default: all bits set (released)
        assert commander._buttons == 0xFFF
        commander._update_state("button_press", {"button": "a"})
        # Bit 0 cleared = A pressed
        assert commander._buttons & 1 == 0

    def test_button_release_updates_state(self, commander):
        commander._update_state("button_press", {"button": "a"})
        commander._update_state("button_release", {"button": "a"})
        assert commander._buttons & 1 == 1

    def test_stick_updates_circle_pad(self, commander):
        commander._update_state("stick", {"x": 0, "y": 0})
        # Center = (128, 128)
        assert commander._circle_pad == (128 << 8) | 128

    def test_stick_extreme_values(self, commander):
        commander._update_state("stick", {"x": 127, "y": -128})
        cx = max(0, min(255, 127 + 128))
        cy = max(0, min(255, -128 + 128))
        assert commander._circle_pad == (cy << 8) | cx

    def test_tap_updates_touch(self, commander):
        commander._update_state("tap", {"x": 160, "y": 120})
        assert commander._touch & (1 << 24)  # Touch active bit

    def test_touch_release(self, commander):
        commander._update_state("tap", {"x": 0, "y": 0})
        commander._update_state("touch_release", {})
        assert commander._touch == 0x02000000  # Default (touch not active)

    def test_reset_restores_defaults(self, commander):
        commander._update_state("button_press", {"button": "a"})
        commander._update_state("tap", {"x": 100, "y": 100})
        commander._update_state("reset", {})
        assert commander._buttons == 0xFFF
        assert commander._touch == 0x02000000

    @pytest.mark.asyncio
    async def test_send_command_not_connected(self, commander):
        result = await commander.send_command("button_press", {"button": "a"})
        # Should try to connect first (connect creates socket)
        assert commander.is_connected is True


# --- VirtualGamepadCommander ---


class TestVirtualGamepadCommander:
    @pytest.fixture
    def commander(self):
        return VirtualGamepadCommander()

    def test_commander_name(self, commander):
        assert commander.commander_name == "virtual-gamepad"

    def test_supported_command_types(self, commander):
        assert "gamepad" in commander.supported_command_types

    def test_not_connected_initially(self, commander):
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect(self, commander):
        await commander.disconnect()
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_unsupported_platform_returns_error(self, commander):
        """On platforms without evdev/vgamepad, send_command returns an error."""
        with patch("airplay_client.commander.virtual_gamepad._SYSTEM", "Darwin"):
            # Force connected state to skip connect
            commander._connected = True
            result = await commander.send_command("button_press", {"button": "a"})
            assert result.success is False
            assert "Unsupported" in result.error
