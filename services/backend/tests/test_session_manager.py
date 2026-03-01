"""Tests for backend session manager."""

import pytest
from unittest.mock import AsyncMock, MagicMock

import numpy as np

from backend.session_manager import ClientSession, SessionManager
from shared.messages import ClientStatus, HIDCommandMessage


class TestClientSession:
    def test_defaults(self):
        ws = MagicMock()
        session = ClientSession(frame_websocket=ws)
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
        assert session.frame_websocket is mock_ws

    def test_get_session_nonexistent(self, manager):
        assert manager.get_session("nope") is None

    @pytest.mark.asyncio
    async def test_send_command(self, manager, mock_ws):
        await manager.register("c1", mock_ws)
        cmd = HIDCommandMessage(action="click", params={"x": 100, "y": 200})
        sent_cmd = await manager.send_command("c1", cmd)
        mock_ws.send_text.assert_called_once()
        sent = mock_ws.send_text.call_args[0][0]
        assert '"click"' in sent
        assert sent_cmd.command_id is not None
        assert sent_cmd.command_sequence == 1

    @pytest.mark.asyncio
    async def test_send_command_prefers_control_channel(self, manager):
        frame_ws = AsyncMock()
        control_ws = AsyncMock()
        await manager.register("c1", frame_ws, channel="frame")
        await manager.register("c1", control_ws, channel="control")
        cmd = HIDCommandMessage(action="press")
        await manager.send_command("c1", cmd)
        control_ws.send_text.assert_called_once()
        frame_ws.send_text.assert_not_called()

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
        sent = await manager.broadcast_command(cmd)
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()
        assert set(sent.keys()) == {"c1", "c2"}

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

    def test_update_frame_existing_session(self, manager):
        import asyncio
        loop = asyncio.get_event_loop()
        ws = AsyncMock()
        loop.run_until_complete(manager.register("c1", ws))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        jpeg = b"\xff\xd8fake\xff\xd9"
        manager.update_frame("c1", frame, jpeg, capture_timestamp=1000.0)
        session = manager.get_session("c1")
        assert session.frames_received == 1
        assert session.latest_frame_sequence == 1
        assert session.latest_frame_jpeg == jpeg
        assert session.last_frame_at == 1000.0

    def test_update_frame_auto_creates_session(self, manager):
        frame = np.zeros((50, 50, 3), dtype=np.uint8)
        jpeg = b"\xff\xd8test\xff\xd9"
        manager.update_frame("rtsp-client", frame, jpeg)
        session = manager.get_session("rtsp-client")
        assert session is not None
        assert session.frames_received == 1
        assert session.latest_frame is not None

    def test_update_frame_computes_latency(self, manager):
        import asyncio, time
        loop = asyncio.get_event_loop()
        ws = AsyncMock()
        loop.run_until_complete(manager.register("c1", ws))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        capture_ts = time.time() - 0.05  # 50ms ago
        manager.update_frame("c1", frame, b"\xff\xd8\xff\xd9", capture_timestamp=capture_ts)
        session = manager.get_session("c1")
        assert session.last_frame_latency_ms is not None
        assert session.last_frame_latency_ms >= 40.0  # at least ~40ms (allowing for execution time)

    def test_update_frame_no_timestamp_no_latency(self, manager):
        import asyncio
        loop = asyncio.get_event_loop()
        ws = AsyncMock()
        loop.run_until_complete(manager.register("c1", ws))
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        manager.update_frame("c1", frame, b"\xff\xd8\xff\xd9")
        session = manager.get_session("c1")
        assert session.last_frame_latency_ms is None

    def test_mark_command_ack_updates_rtt(self, manager):
        ws = AsyncMock()
        import asyncio
        loop = asyncio.get_event_loop()
        session = loop.run_until_complete(manager.register("c1", ws))
        session.pending_commands["cmd-1"] = 0.0
        from shared.messages import CommandAck

        manager.mark_command_ack(
            "c1",
            CommandAck(
                command_id="cmd-1",
                received_at_client=1.0,
                completed_at_client=2.0,
                success=True,
            ),
        )
        assert session.commands_acked == 1
        assert session.last_command_rtt_ms is not None
