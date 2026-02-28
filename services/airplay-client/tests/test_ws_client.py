"""Tests for WebSocket client."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np

from airplay_client.ws_client import WebSocketClient
from shared.messages import ConfigUpdate, HIDCommandMessage


class TestWebSocketClient:
    @pytest.fixture
    def on_hid(self):
        return AsyncMock()

    @pytest.fixture
    def on_config(self):
        return AsyncMock()

    @pytest.fixture
    def ws_client(self, on_hid, on_config):
        return WebSocketClient(
            on_hid_command=on_hid,
            on_config_update=on_config,
            backend_ws_url="ws://localhost:8000/ws/client",
        )

    def test_not_connected_initially(self, ws_client):
        assert ws_client.is_connected is False

    def test_frame_sequence_starts_at_zero(self, ws_client):
        assert ws_client._frame_sequence == 0

    @pytest.mark.asyncio
    async def test_send_frame_when_not_connected(self, ws_client):
        """Should silently skip when not connected."""
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        await ws_client.send_frame(frame)
        # No error raised

    @pytest.mark.asyncio
    async def test_send_status_when_not_connected(self, ws_client):
        from shared.messages import ClientStatus
        status = ClientStatus(airplay_running=True, esp32_reachable=False)
        await ws_client.send_status(status)
        # No error raised

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self, ws_client):
        await ws_client.disconnect()
        assert ws_client.is_connected is False

    @pytest.mark.asyncio
    async def test_send_frame_sends_two_messages(self, ws_client):
        """When connected, send_frame should send metadata then binary."""
        mock_ws = AsyncMock()
        ws_client._ws = mock_ws
        ws_client._connected = True

        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        await ws_client.send_frame(frame, capture_timestamp=1000.0)

        assert mock_ws.send.call_count == 2
        # First call: JSON metadata
        metadata_json = mock_ws.send.call_args_list[0][0][0]
        assert '"frame"' in metadata_json
        assert '"sequence"' in metadata_json
        # Second call: binary JPEG
        jpeg_bytes = mock_ws.send.call_args_list[1][0][0]
        assert isinstance(jpeg_bytes, bytes)

    @pytest.mark.asyncio
    async def test_frame_sequence_increments(self, ws_client):
        mock_ws = AsyncMock()
        ws_client._ws = mock_ws
        ws_client._connected = True

        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        await ws_client.send_frame(frame)
        await ws_client.send_frame(frame)
        assert ws_client._frame_sequence == 2

    @pytest.mark.asyncio
    async def test_send_status_sends_json(self, ws_client):
        mock_ws = AsyncMock()
        ws_client._ws = mock_ws
        ws_client._connected = True

        from shared.messages import ClientStatus
        status = ClientStatus(airplay_running=True, esp32_reachable=True)
        await ws_client.send_status(status)

        mock_ws.send.assert_called_once()
        sent = mock_ws.send.call_args[0][0]
        assert '"client_status"' in sent
