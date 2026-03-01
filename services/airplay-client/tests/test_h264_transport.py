"""Tests for H.264 WebSocket transport and factory integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from airplay_client.transport.h264_ws_transport import H264WebSocketTransport


class TestH264WebSocketTransport:
    @pytest.fixture
    def frame_ws(self):
        ws = MagicMock()
        ws.connect = AsyncMock()
        ws.disconnect = AsyncMock()
        ws.send_h264_au = AsyncMock()
        ws.send_audio_chunk = AsyncMock()
        ws.is_connected = True
        return ws

    @pytest.fixture
    def h264_capture(self):
        capture = MagicMock()
        capture.get_au = MagicMock(return_value=None)
        capture.is_running = True
        return capture

    @pytest.fixture
    def audio_source(self):
        source = MagicMock()
        source.get_chunk = MagicMock(return_value=b"\x00" * 100)
        source.sample_rate = 44100
        source.channels = 2
        return source

    def test_transport_name(self, frame_ws, h264_capture):
        t = H264WebSocketTransport(frame_ws, h264_capture)
        assert t.transport_name == "h264-ws"

    def test_is_connected_delegates(self, frame_ws, h264_capture):
        t = H264WebSocketTransport(frame_ws, h264_capture)
        assert t.is_connected is True
        frame_ws.is_connected = False
        assert t.is_connected is False

    def test_initial_counters(self, frame_ws, h264_capture):
        t = H264WebSocketTransport(frame_ws, h264_capture)
        assert t.frames_captured == 0
        assert t.frames_sent == 0
        assert t.audio_chunks_captured == 0
        assert t.audio_chunks_sent == 0

    @pytest.mark.asyncio
    async def test_stop_disconnects_ws(self, frame_ws, h264_capture):
        t = H264WebSocketTransport(frame_ws, h264_capture)
        await t.stop()
        frame_ws.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_creates_tasks(self, frame_ws, h264_capture, audio_source):
        t = H264WebSocketTransport(frame_ws, h264_capture, audio_source)
        await t.start()
        # Should have 3 tasks: WS connect, H264 sender, audio sender
        assert len(t._tasks) == 3
        await t.stop()

    @pytest.mark.asyncio
    async def test_start_without_audio(self, frame_ws, h264_capture):
        t = H264WebSocketTransport(frame_ws, h264_capture, audio_source=None)
        await t.start()
        # Should have 2 tasks: WS connect, H264 sender
        assert len(t._tasks) == 2
        await t.stop()


class TestTransportFactoryH264WS:
    @patch("airplay_client.transport.factory.client_settings")
    def test_h264_ws_mode(self, mock_settings):
        mock_settings.transport_mode = "h264-ws"

        from airplay_client.capture.h264_capture import H264Capture
        from airplay_client.transport.factory import create_media_transport

        frame_source = MagicMock()
        frame_ws = MagicMock()
        h264_capture = MagicMock(spec=H264Capture)

        transport = create_media_transport(
            frame_source=frame_source,
            audio_source=None,
            frame_ws=frame_ws,
            h264_capture=h264_capture,
        )
        assert transport.transport_name == "h264-ws"

    @patch("airplay_client.transport.factory.client_settings")
    def test_h264_ws_requires_frame_ws(self, mock_settings):
        mock_settings.transport_mode = "h264-ws"

        from airplay_client.capture.h264_capture import H264Capture
        from airplay_client.transport.factory import create_media_transport

        h264_capture = MagicMock(spec=H264Capture)
        with pytest.raises(ValueError, match="frame_ws"):
            create_media_transport(
                frame_source=MagicMock(),
                audio_source=None,
                frame_ws=None,
                h264_capture=h264_capture,
            )

    @patch("airplay_client.transport.factory.client_settings")
    def test_h264_ws_requires_h264_capture(self, mock_settings):
        mock_settings.transport_mode = "h264-ws"

        from airplay_client.transport.factory import create_media_transport

        with pytest.raises(ValueError, match="h264_capture"):
            create_media_transport(
                frame_source=MagicMock(),
                audio_source=None,
                frame_ws=MagicMock(),
                h264_capture=None,
            )

    @patch("airplay_client.transport.factory.client_settings")
    def test_unknown_mode_includes_h264_ws(self, mock_settings):
        mock_settings.transport_mode = "invalid-mode"

        from airplay_client.transport.factory import create_media_transport

        with pytest.raises(ValueError, match="h264-ws"):
            create_media_transport(
                frame_source=MagicMock(),
                audio_source=None,
            )
