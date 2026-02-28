"""Tests for ESP32 HID command client."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from airplay_client.commander.esp32_client import ESP32Client, HIDCommand


class TestHIDCommand:
    def test_move_command(self):
        cmd = HIDCommand.move(10, -5)
        assert cmd.action == "move"
        assert cmd.to_dict() == {"action": "move", "dx": 10, "dy": -5}

    def test_click_command(self):
        cmd = HIDCommand.click(100, 200)
        assert cmd.to_dict() == {"action": "click", "x": 100, "y": 200}

    def test_swipe_command(self):
        cmd = HIDCommand.swipe(0, 0, 100, 200, 500)
        d = cmd.to_dict()
        assert d["action"] == "swipe"
        assert d["duration_ms"] == 500

    def test_swipe_default_duration(self):
        cmd = HIDCommand.swipe(0, 0, 50, 50)
        assert cmd.params["duration_ms"] == 300

    def test_press_command(self):
        assert HIDCommand.press().to_dict() == {"action": "press"}

    def test_release_command(self):
        assert HIDCommand.release().to_dict() == {"action": "release"}


class TestESP32Client:
    @pytest.fixture
    def client(self):
        return ESP32Client(host="192.168.1.50", port=80, timeout=1.0)

    @pytest.mark.asyncio
    async def test_send_move_command(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"action": "move", "status": "ok", "dx": 10, "dy": 5}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.move(10, 5)
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_send_click_command(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"action": "click", "status": "ok"}
        mock_response.raise_for_status = MagicMock()

        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.click(100, 200)
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ping_success(self, client):
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            assert await client.ping() is True

    @pytest.mark.asyncio
    async def test_ping_failure(self, client):
        with patch.object(client._client, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            assert await client.ping() is False

    @pytest.mark.asyncio
    async def test_connection_error_raises(self, client):
        with patch.object(client._client, "post", new_callable=AsyncMock, side_effect=httpx.ConnectError("refused")):
            with pytest.raises(httpx.ConnectError):
                await client.move(10, 10)
