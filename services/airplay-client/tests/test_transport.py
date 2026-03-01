"""Tests for media transport layer (SRT + WebSocket + Failover)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from airplay_client.transport.base import MediaTransport
from airplay_client.transport.failover_transport import FailoverTransport
from airplay_client.transport.srt_transport import SRTStats, SRTTransport
from airplay_client.transport.ws_transport import WebSocketTransport
from airplay_client.transport.factory import create_media_transport


# --- Base ---


class TestMediaTransportABC:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            MediaTransport()


# --- WebSocketTransport ---


class TestWebSocketTransport:
    @pytest.fixture
    def frame_ws(self):
        ws = MagicMock()
        ws.connect = AsyncMock()
        ws.disconnect = AsyncMock()
        ws.send_frame = AsyncMock()
        ws.send_audio_chunk = AsyncMock()
        ws.is_connected = True
        return ws

    @pytest.fixture
    def frame_source(self):
        source = MagicMock()
        source.get_frame = MagicMock(return_value=b"fake-jpeg")
        return source

    @pytest.fixture
    def audio_source(self):
        source = MagicMock()
        source.get_chunk = MagicMock(return_value=b"\x00" * 100)
        source.sample_rate = 44100
        source.channels = 2
        return source

    def test_transport_name(self, frame_ws, frame_source):
        t = WebSocketTransport(frame_ws, frame_source)
        assert t.transport_name == "websocket"

    def test_is_connected_delegates(self, frame_ws, frame_source):
        t = WebSocketTransport(frame_ws, frame_source)
        assert t.is_connected is True
        frame_ws.is_connected = False
        assert t.is_connected is False

    def test_initial_counters(self, frame_ws, frame_source):
        t = WebSocketTransport(frame_ws, frame_source)
        assert t.frames_captured == 0
        assert t.frames_sent == 0
        assert t.audio_chunks_captured == 0
        assert t.audio_chunks_sent == 0

    @pytest.mark.asyncio
    async def test_stop_disconnects_ws(self, frame_ws, frame_source):
        t = WebSocketTransport(frame_ws, frame_source)
        await t.stop()
        frame_ws.disconnect.assert_awaited_once()


# --- SRTTransport ---


class TestSRTTransport:
    def test_transport_name(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        assert t.transport_name == "srt"

    def test_not_connected_initially(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        assert t.is_connected is False

    @patch("airplay_client.transport.srt_transport.settings")
    def test_build_srt_url_from_config(self, mock_settings):
        mock_settings.srt_backend_url = "srt://myhost:8890"
        mock_settings.srt_stream_id = ""
        mock_settings.client_id = "test-client"
        url = SRTTransport._build_srt_url()
        assert "srt://myhost:8890" in url
        assert "streamid=publish/chromacatch/test-client" in url

    @patch("airplay_client.transport.srt_transport.settings")
    def test_build_srt_url_from_ws_url(self, mock_settings):
        mock_settings.srt_backend_url = ""
        mock_settings.backend_ws_url = "ws://backend.example.com:8000/ws/client"
        mock_settings.srt_stream_id = ""
        mock_settings.client_id = "station-1"
        url = SRTTransport._build_srt_url()
        assert "srt://backend.example.com:8890" in url
        assert "streamid=publish/chromacatch/station-1" in url

    @patch("airplay_client.transport.srt_transport.settings")
    def test_custom_stream_id(self, mock_settings):
        mock_settings.srt_backend_url = "srt://host:8890"
        mock_settings.srt_stream_id = "custom/path"
        mock_settings.client_id = "c1"
        url = SRTTransport._build_srt_url()
        assert "streamid=custom/path" in url

    @patch("airplay_client.transport.srt_transport.settings")
    def test_url_with_existing_streamid_not_duplicated(self, mock_settings):
        mock_settings.srt_backend_url = "srt://host:8890?streamid=existing"
        mock_settings.srt_stream_id = ""
        mock_settings.client_id = "c1"
        url = SRTTransport._build_srt_url()
        assert url == "srt://host:8890?streamid=existing"

    @patch("airplay_client.transport.srt_transport.shutil.which", return_value="/usr/bin/gst-launch-1.0")
    @patch("airplay_client.transport.srt_transport.settings")
    def test_build_pipeline_has_srtsink(self, mock_settings, mock_which):
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.srt_latency_ms = 50
        mock_settings.srt_passphrase = ""
        mock_settings.audio_enabled = True
        mock_settings.audio_sample_rate = 44100
        mock_settings.audio_channels = 2
        mock_settings.srt_opus_bitrate = 128000
        t = SRTTransport(srt_url="srt://localhost:8890?streamid=test")
        args = t._build_gst_pipeline_args()
        assert "srtsink" in args
        assert "h264parse" in args
        assert "config-interval=-1" in args
        assert "mpegtsmux" in args
        assert "opusenc" in args

    @patch("airplay_client.transport.srt_transport.shutil.which", return_value="/usr/bin/gst-launch-1.0")
    @patch("airplay_client.transport.srt_transport.settings")
    def test_build_pipeline_no_audio(self, mock_settings, mock_which):
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.srt_latency_ms = 50
        mock_settings.srt_passphrase = ""
        t = SRTTransport(srt_url="srt://localhost:8890", audio_enabled=False)
        args = t._build_gst_pipeline_args()
        assert "srtsink" in args
        assert "opusenc" not in args

    @patch("airplay_client.transport.srt_transport.shutil.which", return_value=None)
    def test_missing_gstreamer_raises(self, mock_which):
        t = SRTTransport(srt_url="srt://localhost:8890")
        with pytest.raises(RuntimeError, match="gst-launch-1.0 not found"):
            t._build_gst_pipeline_args()

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        await t.stop()  # Should not raise
        assert t.is_connected is False


# --- Factory ---


class TestTransportFactory:
    @patch("airplay_client.transport.factory.client_settings")
    def test_creates_ws_transport(self, mock_settings):
        mock_settings.transport_mode = "websocket"
        frame_ws = MagicMock()
        frame_source = MagicMock()
        t = create_media_transport(frame_source=frame_source, audio_source=None, frame_ws=frame_ws)
        assert isinstance(t, WebSocketTransport)

    @patch("airplay_client.transport.factory.client_settings")
    def test_creates_srt_transport(self, mock_settings):
        mock_settings.transport_mode = "srt"
        mock_settings.srt_backend_url = "srt://host:8890"
        mock_settings.srt_stream_id = ""
        mock_settings.client_id = "c1"
        frame_source = MagicMock()
        t = create_media_transport(frame_source=frame_source, audio_source=None)
        assert isinstance(t, SRTTransport)

    @patch("airplay_client.transport.factory.client_settings")
    def test_creates_failover_transport(self, mock_settings):
        mock_settings.transport_mode = "srt-failover"
        mock_settings.srt_backend_url = "srt://host:8890"
        mock_settings.srt_stream_id = ""
        mock_settings.client_id = "c1"
        frame_ws = MagicMock()
        frame_source = MagicMock()
        t = create_media_transport(frame_source=frame_source, audio_source=None, frame_ws=frame_ws)
        assert isinstance(t, FailoverTransport)

    @patch("airplay_client.transport.factory.client_settings")
    def test_failover_requires_frame_ws(self, mock_settings):
        mock_settings.transport_mode = "srt-failover"
        mock_settings.srt_backend_url = "srt://host:8890"
        mock_settings.srt_stream_id = ""
        mock_settings.client_id = "c1"
        with pytest.raises(ValueError, match="frame_ws"):
            create_media_transport(frame_source=MagicMock(), audio_source=None)

    @patch("airplay_client.transport.factory.client_settings")
    def test_unknown_mode_raises(self, mock_settings):
        mock_settings.transport_mode = "carrier_pigeon"
        with pytest.raises(ValueError, match="carrier_pigeon"):
            create_media_transport(frame_source=MagicMock(), audio_source=None)


# --- SRT Stats ---


class TestSRTStats:
    def test_defaults(self):
        stats = SRTStats()
        assert stats.rtt_ms is None
        assert stats.bandwidth_kbps is None
        assert stats.packet_loss_pct is None

    def test_parse_rtt(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        t._parse_srt_line("SRT: RTT=12.5ms, bandwidth=2500kbps")
        assert t.stats.rtt_ms == 12.5

    def test_parse_bandwidth(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        t._parse_srt_line("bandwidth=2500kbps")
        assert t.stats.bandwidth_kbps == 2500.0

    def test_parse_loss(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        t._parse_srt_line("packet loss=0.1%")
        assert t.stats.packet_loss_pct == 0.1

    def test_parse_all_stats(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        t._parse_srt_line("SRT: RTT=8.2ms, bandwidth=3000kbps, loss=0.05%")
        assert t.stats.rtt_ms == 8.2
        assert t.stats.bandwidth_kbps == 3000.0
        assert t.stats.packet_loss_pct == 0.05

    def test_parse_no_match(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        t._parse_srt_line("some random log line")
        assert t.stats.rtt_ms is None
        assert t.stats.bandwidth_kbps is None
        assert t.stats.packet_loss_pct is None

    def test_parse_colon_format(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        t._parse_srt_line("RTT: 15.0 ms")
        assert t.stats.rtt_ms == 15.0

    def test_restart_count_starts_at_zero(self):
        t = SRTTransport(srt_url="srt://localhost:8890")
        assert t.restart_count == 0


# --- FailoverTransport ---


class TestFailoverTransport:
    @pytest.fixture
    def srt(self):
        t = MagicMock(spec=SRTTransport)
        t.start = AsyncMock()
        t.stop = AsyncMock()
        t.is_connected = False
        t.transport_name = "srt"
        t.restart_count = 0
        t.stats = SRTStats(rtt_ms=10.0)
        t.frames_captured = 0
        t.frames_sent = 0
        t.audio_chunks_captured = 0
        t.audio_chunks_sent = 0
        return t

    @pytest.fixture
    def ws(self):
        t = MagicMock(spec=WebSocketTransport)
        t.start = AsyncMock()
        t.stop = AsyncMock()
        t.is_connected = True
        t.transport_name = "websocket"
        t.frames_captured = 10
        t.frames_sent = 8
        t.audio_chunks_captured = 5
        t.audio_chunks_sent = 4
        return t

    def test_transport_name_srt_mode(self, srt, ws):
        ft = FailoverTransport(srt_transport=srt, ws_transport=ws)
        assert ft.transport_name == "srt"

    def test_transport_name_fallback_mode(self, srt, ws):
        ft = FailoverTransport(srt_transport=srt, ws_transport=ws)
        ft._using_fallback = True
        assert ft.transport_name == "websocket (srt-fallback)"

    def test_is_connected_delegates_to_active(self, srt, ws):
        ft = FailoverTransport(srt_transport=srt, ws_transport=ws)
        srt.is_connected = True
        assert ft.is_connected is True
        srt.is_connected = False
        assert ft.is_connected is False

    def test_stats_always_from_srt(self, srt, ws):
        ft = FailoverTransport(srt_transport=srt, ws_transport=ws)
        assert ft.stats.rtt_ms == 10.0
        ft._using_fallback = True
        ft._active = ws
        assert ft.stats.rtt_ms == 10.0

    def test_counters_delegate_to_active(self, srt, ws):
        ft = FailoverTransport(srt_transport=srt, ws_transport=ws)
        assert ft.frames_captured == 0
        ft._active = ws
        ft._using_fallback = True
        assert ft.frames_captured == 10
        assert ft.frames_sent == 8
        assert ft.audio_chunks_captured == 5
        assert ft.audio_chunks_sent == 4

    @pytest.mark.asyncio
    async def test_stop_cancels_monitor(self, srt, ws):
        ft = FailoverTransport(srt_transport=srt, ws_transport=ws)
        ft._running = True
        ft._failover_task = asyncio.create_task(asyncio.sleep(100))
        await ft.stop()
        assert ft._failover_task is None
        srt.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_also_stops_ws_in_fallback(self, srt, ws):
        ft = FailoverTransport(srt_transport=srt, ws_transport=ws)
        ft._running = True
        ft._using_fallback = True
        await ft.stop()
        srt.stop.assert_awaited_once()
        ws.stop.assert_awaited_once()
