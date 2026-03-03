"""Tests for Commander abstraction + factory + ESP32Commander."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from airplay_client.commander.base import Commander, CommandResult
from airplay_client.commander.esp32_commander import ESP32Commander
from airplay_client.commander.factory import create_commander


# --- CommandResult ---


class TestCommandResult:
    def test_success_result(self):
        r = CommandResult(success=True, forwarded_at=1.0, completed_at=2.0)
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        r = CommandResult(success=False, error="connection refused")
        assert r.success is False
        assert r.error == "connection refused"


# --- Commander ABC ---


class TestCommanderABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            Commander()


# --- ESP32Commander ---


class TestESP32Commander:
    @pytest.fixture
    def mock_esp32(self):
        esp32 = MagicMock()
        esp32.send_command = AsyncMock(return_value={"status": "ok"})
        esp32.ping = AsyncMock(return_value=True)
        esp32.close = AsyncMock()
        esp32.status = AsyncMock(return_value={"ble_connected": True})
        esp32.host = "192.168.1.100"
        esp32.port = 80
        return esp32

    @pytest.fixture
    def commander(self, mock_esp32):
        return ESP32Commander(esp32_client=mock_esp32)

    def test_commander_name(self, commander):
        assert commander.commander_name == "esp32"

    def test_supported_command_types(self, commander):
        assert "mouse" in commander.supported_command_types
        assert "keyboard" in commander.supported_command_types

    @pytest.mark.asyncio
    async def test_send_command_success(self, commander, mock_esp32):
        result = await commander.send_command("click", {"x": 100, "y": 200})
        assert result.success is True
        assert result.error is None
        mock_esp32.send_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_command_failure(self, commander, mock_esp32):
        mock_esp32.send_command = AsyncMock(side_effect=ConnectionError("refused"))
        result = await commander.send_command("click", {"x": 0, "y": 0})
        assert result.success is False
        assert "refused" in result.error

    @pytest.mark.asyncio
    async def test_connect_success(self, commander, mock_esp32):
        await commander.connect()
        assert commander.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_unreachable(self, commander, mock_esp32):
        mock_esp32.ping = AsyncMock(return_value=False)
        await commander.connect()
        assert commander.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect(self, commander, mock_esp32):
        await commander.connect()
        await commander.disconnect()
        assert commander.is_connected is False
        mock_esp32.close.assert_called_once()


# --- Commander Factory ---


class TestCommanderFactory:
    @patch("airplay_client.commander.factory.client_settings")
    def test_create_esp32(self, mock_settings):
        mock_settings.commander_mode = "esp32"
        commander = create_commander()
        assert isinstance(commander, ESP32Commander)

    @patch("airplay_client.commander.factory.client_settings")
    def test_create_sysbotbase(self, mock_settings):
        mock_settings.commander_mode = "sysbotbase"
        mock_settings.commander_host = "192.168.1.50"
        mock_settings.commander_port = 6000
        commander = create_commander()
        assert commander.commander_name == "sysbotbase"

    @patch("airplay_client.commander.factory.client_settings")
    def test_create_luma3ds(self, mock_settings):
        mock_settings.commander_mode = "luma3ds"
        mock_settings.commander_host = "192.168.1.60"
        mock_settings.commander_port = 4950
        commander = create_commander()
        assert commander.commander_name == "luma3ds"

    @patch("airplay_client.commander.factory.client_settings")
    def test_create_virtual_gamepad(self, mock_settings):
        mock_settings.commander_mode = "virtual-gamepad"
        commander = create_commander()
        assert commander.commander_name == "virtual-gamepad"

    @patch("airplay_client.commander.factory.client_settings")
    def test_unknown_mode_raises(self, mock_settings):
        mock_settings.commander_mode = "unknown"
        with pytest.raises(ValueError, match="Unknown commander mode"):
            create_commander()
