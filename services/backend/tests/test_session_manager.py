"""Tests for backend session manager."""

import pytest
from unittest.mock import AsyncMock, MagicMock

import numpy as np

from backend.session_manager import ClientSession, SessionManager
from shared.messages import ClientStatus, HIDCommandMessage


class TestClientSession:
    def test_defaults(self):
        ws = MagicMock()
        session = ClientSession(websocket=ws)
        assert session.connected_at > 0
        assert session.last_frame_at == 0.0
        assert session.last_status is None
        assert session.frames_received == 0
        assert session.latest_frame is None


class TestSessionManager:
    @pytest.fixture
    def manager(self):
        return SessionManager()

    @pytest.fixture
    def mock_ws(self):
        ws = AsyncMock()
        ws.send_text = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_register_client(self, manager, mock_ws):
        session = await manager.register("client-1", mock_ws)
        assert isinstance(session, ClientSession)
        assert "client-1" in manager.connected_clients

    @pytest.mark.asyncio
    async def test_unregister_client(self, manager, mock_ws):
        await manager.register("client-1", mock_ws)
        await manager.unregister("client-1")
        assert "client-1" not in manager.connected_clients

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self, manager):
        # Should not raise
        await manager.unregister("nonexistent")

    @pytest.mark.asyncio
    async def test_get_session(self, manager, mock_ws):
        await manager.register("c1", mock_ws)
        session = manager.get_session("c1")
        assert session is not None
        assert session.websocket is mock_ws

    def test_get_session_nonexistent(self, manager):
        assert manager.get_session("nope") is None

    @pytest.mark.asyncio
    async def test_send_command(self, manager, mock_ws):
        await manager.register("c1", mock_ws)
        cmd = HIDCommandMessage(action="click", params={"x": 100, "y": 200})
        await manager.send_command("c1", cmd)
        mock_ws.send_text.assert_called_once()
        sent = mock_ws.send_text.call_args[0][0]
        assert '"click"' in sent

    @pytest.mark.asyncio
    async def test_send_command_unknown_client(self, manager):
        cmd = HIDCommandMessage(action="click", params={"x": 0, "y": 0})
        with pytest.raises(ValueError, match="No client connected"):
            await manager.send_command("unknown", cmd)

    @pytest.mark.asyncio
    async def test_broadcast_command(self, manager):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        await manager.register("c1", ws1)
        await manager.register("c2", ws2)
        cmd = HIDCommandMessage(action="press")
        await manager.broadcast_command(cmd)
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_connected_clients(self, manager, mock_ws):
        assert manager.connected_clients == []
        await manager.register("a", mock_ws)
        await manager.register("b", mock_ws)
        assert sorted(manager.connected_clients) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_get_latest_frame(self, manager, mock_ws):
        session = await manager.register("c1", mock_ws)
        assert manager.get_latest_frame("c1") is None
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        session.latest_frame = frame
        result = manager.get_latest_frame("c1")
        assert result is not None
        assert result.shape == (100, 100, 3)

    def test_get_latest_frame_nonexistent(self, manager):
        assert manager.get_latest_frame("nope") is None
