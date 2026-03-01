"""Factory for creating the appropriate media transport."""

import logging

from airplay_client.audio.base import AudioSource
from airplay_client.capture.h264_capture import H264Capture
from airplay_client.config import client_settings
from airplay_client.sources.base import FrameSource
from airplay_client.transport.base import MediaTransport
from airplay_client.ws_client import WebSocketClient

logger = logging.getLogger(__name__)


def create_media_transport(
    frame_source: FrameSource,
    audio_source: AudioSource | None,
    frame_ws: WebSocketClient | None = None,
    h264_capture: H264Capture | None = None,
) -> MediaTransport:
    """Create a media transport based on config.

    Args:
        frame_source: The frame source (AirPlay, capture card, screen).
        audio_source: The audio source (or None if disabled).
        frame_ws: WebSocket client for frame channel (only needed for WS transport).
        h264_capture: H.264 capture instance (only needed for h264-ws transport).

    Returns:
        A MediaTransport instance (SRT, WebSocket, or H.264-WS).
    """
    mode = client_settings.transport_mode.lower()

    if mode == "srt":
        from airplay_client.transport.srt_transport import SRTTransport
        logger.info("Using SRT media transport (H.264 passthrough + Opus audio)")
        return SRTTransport(audio_enabled=audio_source is not None)
    elif mode == "srt-failover":
        from airplay_client.transport.failover_transport import FailoverTransport
        from airplay_client.transport.srt_transport import SRTTransport
        from airplay_client.transport.ws_transport import WebSocketTransport
        if frame_ws is None:
            raise ValueError("SRT failover requires a frame_ws client for fallback")
        srt = SRTTransport(audio_enabled=audio_source is not None)
        ws = WebSocketTransport(frame_ws=frame_ws, frame_source=frame_source, audio_source=audio_source)
        logger.info("Using SRT media transport with WebSocket failover")
        return FailoverTransport(srt_transport=srt, ws_transport=ws)
    elif mode == "h264-ws":
        from airplay_client.transport.h264_ws_transport import H264WebSocketTransport
        if frame_ws is None:
            raise ValueError("H.264-WS transport requires a frame_ws client")
        if h264_capture is None:
            raise ValueError("H.264-WS transport requires an h264_capture instance")
        logger.info("Using H.264 passthrough WebSocket transport")
        return H264WebSocketTransport(
            frame_ws=frame_ws,
            h264_capture=h264_capture,
            audio_source=audio_source,
        )
    elif mode == "websocket":
        from airplay_client.transport.ws_transport import WebSocketTransport
        if frame_ws is None:
            raise ValueError("WebSocket transport requires a frame_ws client")
        logger.info("Using WebSocket media transport (JPEG frames + PCM audio)")
        return WebSocketTransport(
            frame_ws=frame_ws,
            frame_source=frame_source,
            audio_source=audio_source,
        )
    else:
        raise ValueError(f"Unknown transport mode: {mode!r}. Use 'srt', 'srt-failover', 'h264-ws', or 'websocket'.")
