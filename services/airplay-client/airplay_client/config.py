"""Configuration for the local client."""

import socket

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

    # ESP32 connection
    esp32_host: str = "192.168.1.100"
    esp32_port: int = 80
    esp32_timeout: float = 2.0

    # AirPlay / UxPlay
    airplay_udp_port: int = 5000
    airplay_audio_udp_port: int = 5002
    airplay_name: str = "ChromaCatch"
    uxplay_path: str = "uxplay"

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

    # WebSocket resilience
    ws_reconnect_delay: float = 1.0
    ws_reconnect_max_delay: float = 30.0
    ws_heartbeat_interval: float = 10.0

    model_config = {"env_file": ".env", "env_prefix": "CC_CLIENT_"}


client_settings = ClientSettings()
