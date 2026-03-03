"""Configuration for the local client."""

import socket
import tempfile

from pydantic_settings import BaseSettings

from shared.constants import (
    DEFAULT_AUDIO_CHANNELS,
    DEFAULT_AUDIO_CHUNK_MS,
    DEFAULT_AUDIO_SAMPLE_RATE,
    DEFAULT_FRAME_INTERVAL_MS,
    DEFAULT_JPEG_QUALITY,
    DEFAULT_MAX_DIMENSION,
)


class ClientSettings(BaseSettings):
    # Backend connection
    backend_ws_url: str = "ws://localhost:8000/ws/client"
    backend_control_ws_url: str | None = None
    client_id: str = socket.gethostname()
    api_key: str = ""

    # Commander (input target)
    commander_mode: str = "esp32"  # esp32 | sysbotbase | luma3ds | virtual-gamepad
    commander_host: str = ""  # Target host (defaults to esp32_host for esp32 mode)
    commander_port: int = 0  # Target port (defaults to esp32_port for esp32 mode)

    # ESP32 connection (used when commander_mode="esp32")
    esp32_host: str = "192.168.1.100"
    esp32_port: int = 80
    esp32_timeout: float = 2.0

    # AirPlay / UxPlay
    airplay_udp_port: int = 5000
    airplay_audio_udp_port: int = 5002
    airplay_name: str = "ChromaCatch"
    uxplay_path: str = "uxplay"
    cleanup_stale_airplay_processes: bool = True

    # Frame capture
    capture_source: str = "airplay"  # airplay | capture | screen
    capture_device: str = "0"
    frame_width: int = 1920
    frame_height: int = 1080
    capture_width: int = 0
    capture_height: int = 0
    capture_fps: int = 30
    target_fps: int = 30
    screen_monitor: int = 1
    screen_region: str = ""  # x,y,width,height
    airplay_reconnect_timeout_s: float = 8.0

    # Frame encoding for transport
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    max_dimension: int = DEFAULT_MAX_DIMENSION
    frame_interval_ms: int = DEFAULT_FRAME_INTERVAL_MS

    # Audio transport
    audio_enabled: bool = True
    audio_source: str = "auto"  # auto | airplay | system | none
    audio_sample_rate: int = DEFAULT_AUDIO_SAMPLE_RATE
    audio_channels: int = DEFAULT_AUDIO_CHANNELS
    audio_chunk_ms: int = DEFAULT_AUDIO_CHUNK_MS
    audio_input_backend: str = "auto"  # auto | avfoundation | pulse | dshow
    audio_input_device: str = ""  # backend-specific input selector

    # Transport mode: "webrtc" | "srt" | "srt-failover" | "h264-ws" | "websocket" | "webrtc-failover"
    # webrtc: H.264 passthrough via GStreamer WHIP to MediaMTX (lowest latency, UDP)
    # h264-ws: H.264 passthrough over WebSocket (Cloud Run compatible, near-SRT efficiency)
    transport_mode: str = "websocket"  # default to websocket until backend is configured

    # WebRTC transport settings (used when transport_mode="webrtc")
    webrtc_whip_url: str = ""  # e.g. http://host:8889/chromacatch/whip (auto-derived if empty)
    webrtc_stun_server: str = "stun://stun.l.google.com:19302"
    webrtc_turn_server: str = ""  # optional TURN relay for symmetric NATs
    webrtc_turn_username: str = ""
    webrtc_turn_password: str = ""

    # SRT transport settings (used when transport_mode="srt")
    srt_backend_url: str = ""  # e.g. srt://host:8890
    srt_latency_ms: int = 50  # SRT latency buffer (ms)
    srt_passphrase: str = ""  # optional SRT encryption passphrase
    srt_stream_id: str = ""  # auto-derived from client_id if empty
    srt_opus_bitrate: int = 128000  # Opus audio bitrate (bps)

    # WebSocket resilience
    ws_reconnect_delay: float = 1.0
    ws_reconnect_max_delay: float = 30.0
    ws_heartbeat_interval: float = 3.0
    ws_ssl_verify: bool = True  # set False to skip SSL cert verification

    # Process lifecycle
    single_instance_lock_path: str = tempfile.gettempdir()

    model_config = {"env_file": ".env", "env_prefix": "CC_CLIENT_"}


client_settings = ClientSettings()
