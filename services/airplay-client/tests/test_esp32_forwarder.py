"""Tests for ESP32 command forwarder."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from airplay_client.esp32_forwarder import ESP32Forwarder
from shared.messages import HIDCommandMessage


class TestESP32Forwarder:
    @pytest.fixture
    def mock_esp32(self):
        esp32 = MagicMock()
        esp32.send_command = AsyncMock(return_value={"status": "ok"})
        return esp32

    @pytest.fixture
    def forwarder(self, mock_esp32):
        return ESP32Forwarder(mock_esp32)

    @pytest.mark.asyncio
    async def test_forward_click(self, forwarder, mock_esp32):
        msg = HIDCommandMessage(action="click", params={"x": 100, "y": 200})
        await forwarder.handle_command(msg)
        mock_esp32.send_command.assert_called_once()
        cmd = mock_esp32.send_command.call_args[0][0]
        assert cmd.action == "click"
        assert cmd.params == {"x": 100, "y": 200}

    @pytest.mark.asyncio
    async def test_forward_move(self, forwarder, mock_esp32):
        msg = HIDCommandMessage(action="move", params={"dx": 5, "dy": -10})
        await forwarder.handle_command(msg)
        cmd = mock_esp32.send_command.call_args[0][0]
        assert cmd.action == "move"

    @pytest.mark.asyncio
    async def test_forward_swipe(self, forwarder, mock_esp32):
        msg = HIDCommandMessage(
            action="swipe",
            params={"x1": 0, "y1": 0, "x2": 100, "y2": 200, "duration_ms": 500},
        )
        await forwarder.handle_command(msg)
        cmd = mock_esp32.send_command.call_args[0][0]
        assert cmd.action == "swipe"
        assert cmd.params["duration_ms"] == 500

    @pytest.mark.asyncio
    async def test_forward_press(self, forwarder, mock_esp32):
        msg = HIDCommandMessage(action="press")
        await forwarder.handle_command(msg)
        cmd = mock_esp32.send_command.call_args[0][0]
        assert cmd.action == "press"

    @pytest.mark.asyncio
    async def test_forward_error_logged_not_raised(self, forwarder, mock_esp32):
        mock_esp32.send_command = AsyncMock(side_effect=ConnectionError("refused"))
        msg = HIDCommandMessage(action="click", params={"x": 0, "y": 0})
        # Should not raise
        await forwarder.handle_command(msg)

    @pytest.mark.asyncio
    async def test_forward_empty_params(self, forwarder, mock_esp32):
        msg = HIDCommandMessage(action="release", params={})
        await forwarder.handle_command(msg)
        cmd = mock_esp32.send_command.call_args[0][0]
        assert cmd.action == "release"
        assert cmd.params == {}
