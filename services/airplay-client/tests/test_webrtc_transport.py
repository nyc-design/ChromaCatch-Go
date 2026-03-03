"""Tests for WebRTC transport (GStreamer whipclientsink)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from airplay_client.transport.webrtc_transport import WebRTCTransport


class TestWebRTCTransport:
    def test_transport_name(self):
        t = WebRTCTransport(video_udp_port=5000, whip_url="http://localhost:8889/test/whip")
        assert t.transport_name == "webrtc"

    def test_not_connected_initially(self):
        t = WebRTCTransport(video_udp_port=5000, whip_url="http://localhost:8889/test/whip")
        assert t.is_connected is False

    def test_restart_count_initially_zero(self):
        t = WebRTCTransport(video_udp_port=5000, whip_url="http://localhost:8889/test/whip")
        assert t.restart_count == 0

    @patch("airplay_client.transport.webrtc_transport.settings")
    def test_build_whip_url_from_config(self, mock_settings):
        mock_settings.webrtc_whip_url = "http://mediamtx:8889/stream/whip"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.audio_enabled = True
        t = WebRTCTransport()
        assert t._whip_url == "http://mediamtx:8889/stream/whip"

    @patch("airplay_client.transport.webrtc_transport.settings")
    def test_build_whip_url_from_backend_ws(self, mock_settings):
        mock_settings.webrtc_whip_url = ""
        mock_settings.backend_ws_url = "ws://example.com:8000/ws/client"
        mock_settings.srt_stream_id = ""
        mock_settings.client_id = "test-client"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.audio_enabled = False
        t = WebRTCTransport()
        assert "example.com" in t._whip_url
        assert "8889" in t._whip_url
        assert "whip" in t._whip_url

    @patch("shutil.which", return_value="/usr/bin/gst-launch-1.0")
    @patch("airplay_client.transport.webrtc_transport.settings")
    def test_gst_pipeline_video_only(self, mock_settings, mock_which):
        mock_settings.webrtc_whip_url = "http://localhost:8889/test/whip"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.audio_enabled = False
        mock_settings.webrtc_stun_server = ""
        mock_settings.webrtc_turn_server = ""
        t = WebRTCTransport(audio_enabled=False)
        args = t._build_gst_pipeline_args()
        assert "whipclientsink" in args
        assert "udpsrc" in args
        assert "rtph264depay" in args
        # No audio elements
        assert "opusenc" not in args

    @patch("shutil.which", return_value="/usr/bin/gst-launch-1.0")
    @patch("airplay_client.transport.webrtc_transport.settings")
    def test_gst_pipeline_with_audio(self, mock_settings, mock_which):
        mock_settings.webrtc_whip_url = "http://localhost:8889/test/whip"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.audio_enabled = True
        mock_settings.audio_sample_rate = 44100
        mock_settings.audio_channels = 2
        mock_settings.srt_opus_bitrate = 128000
        mock_settings.webrtc_stun_server = ""
        mock_settings.webrtc_turn_server = ""
        t = WebRTCTransport(audio_enabled=True)
        args = t._build_gst_pipeline_args()
        assert "opusenc" in args
        assert "rtpopuspay" in args
        assert "whip.sink_1" in args

    @patch("shutil.which", return_value="/usr/bin/gst-launch-1.0")
    @patch("airplay_client.transport.webrtc_transport.settings")
    def test_gst_pipeline_with_stun(self, mock_settings, mock_which):
        mock_settings.webrtc_whip_url = "http://localhost:8889/test/whip"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.audio_enabled = False
        mock_settings.webrtc_stun_server = "stun://stun.l.google.com:19302"
        mock_settings.webrtc_turn_server = ""
        t = WebRTCTransport(audio_enabled=False)
        args = t._build_gst_pipeline_args()
        stun_args = [a for a in args if "stun" in a.lower()]
        assert len(stun_args) >= 1

    @patch("shutil.which", return_value=None)
    @patch("airplay_client.transport.webrtc_transport.settings")
    def test_gst_not_found_raises(self, mock_settings, mock_which):
        mock_settings.webrtc_whip_url = "http://localhost:8889/test/whip"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.audio_enabled = False
        t = WebRTCTransport(audio_enabled=False)
        with pytest.raises(RuntimeError, match="gst-launch-1.0 not found"):
            t._build_gst_pipeline_args()

    @pytest.mark.asyncio
    @patch("airplay_client.transport.webrtc_transport.settings")
    async def test_stop_without_start(self, mock_settings):
        mock_settings.webrtc_whip_url = "http://localhost:8889/test/whip"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.audio_enabled = False
        t = WebRTCTransport(audio_enabled=False)
        await t.stop()  # Should not raise
        assert t.is_connected is False


class TestWebRTCTransportFactory:
    @patch("airplay_client.transport.factory.client_settings")
    def test_factory_creates_webrtc(self, mock_settings):
        mock_settings.transport_mode = "webrtc"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.webrtc_whip_url = "http://localhost:8889/test/whip"
        mock_settings.audio_enabled = False

        from airplay_client.transport.factory import create_media_transport

        t = create_media_transport(
            frame_source=MagicMock(),
            audio_source=None,
        )
        assert t.transport_name == "webrtc"

    @patch("airplay_client.transport.factory.client_settings")
    def test_factory_creates_webrtc_failover(self, mock_settings):
        mock_settings.transport_mode = "webrtc-failover"
        mock_settings.airplay_udp_port = 5000
        mock_settings.airplay_audio_udp_port = 5002
        mock_settings.webrtc_whip_url = "http://localhost:8889/test/whip"
        mock_settings.audio_enabled = False

        from airplay_client.transport.factory import create_media_transport

        frame_ws = MagicMock()
        t = create_media_transport(
            frame_source=MagicMock(),
            audio_source=None,
            frame_ws=frame_ws,
        )
        # FailoverTransport delegates transport_name to active (primary = webrtc)
        from airplay_client.transport.failover_transport import FailoverTransport
        assert isinstance(t, FailoverTransport)

    @patch("airplay_client.transport.factory.client_settings")
    def test_factory_webrtc_failover_requires_frame_ws(self, mock_settings):
        mock_settings.transport_mode = "webrtc-failover"

        from airplay_client.transport.factory import create_media_transport

        with pytest.raises(ValueError, match="WebRTC failover requires"):
            create_media_transport(
                frame_source=MagicMock(),
                audio_source=None,
                frame_ws=None,
            )
