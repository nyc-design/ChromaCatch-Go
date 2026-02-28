"""Configuration for the local client."""

from pydantic_settings import BaseSettings

from shared.constants import DEFAULT_FRAME_INTERVAL_MS, DEFAULT_JPEG_QUALITY, DEFAULT_MAX_DIMENSION


class ClientSettings(BaseSettings):
    # Backend connection
    backend_ws_url: str = "ws://localhost:8000/ws/client"
    api_key: str = ""

    # ESP32 connection
    esp32_host: str = "192.168.1.100"
    esp32_port: int = 80
    esp32_timeout: float = 2.0

    # AirPlay / UxPlay
    airplay_udp_port: int = 5000
    airplay_name: str = "ChromaCatch"
    uxplay_path: str = "uxplay"

    # Frame capture
    frame_width: int = 1920
    frame_height: int = 1080
    target_fps: int = 15

    # Frame encoding for transport
    jpeg_quality: int = DEFAULT_JPEG_QUALITY
    max_dimension: int = DEFAULT_MAX_DIMENSION
    frame_interval_ms: int = DEFAULT_FRAME_INTERVAL_MS

    # WebSocket resilience
    ws_reconnect_delay: float = 1.0
    ws_reconnect_max_delay: float = 30.0
    ws_heartbeat_interval: float = 10.0

    model_config = {"env_file": ".env", "env_prefix": "CC_CLIENT_"}


client_settings = ClientSettings()
