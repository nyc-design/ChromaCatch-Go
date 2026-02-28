"""Protocol constants shared between client and backend."""

import logging

PROTOCOL_VERSION = "1.0"
DEFAULT_WS_PORT = 8000
DEFAULT_WS_PATH = "/ws/client"


LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    """Configure logging with a consistent format across all services."""
    logging.basicConfig(level=level, format=LOG_FORMAT)


def make_auth_headers(api_key: str | None) -> dict[str, str]:
    """Build auth headers dict from an API key. Returns empty dict if no key."""
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}

# Frame encoding defaults
DEFAULT_JPEG_QUALITY = 70
DEFAULT_MAX_DIMENSION = 960
DEFAULT_FRAME_INTERVAL_MS = 200  # 5 FPS to backend


class MessageType:
    """WebSocket message type identifiers."""

    # Client -> Backend
    FRAME = "frame"
    CLIENT_STATUS = "client_status"

    # Backend -> Client
    HID_COMMAND = "hid_command"
    CONFIG_UPDATE = "config_update"

    # Bidirectional
    ERROR = "error"
    PING = "ping"
    PONG = "pong"
