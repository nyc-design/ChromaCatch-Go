"""WebSocket message definitions for client-backend protocol.

All messages are Pydantic models that serialize to JSON. Frames use a
two-message pattern: JSON metadata followed by binary JPEG bytes.
"""

from __future__ import annotations

import json
import time

from pydantic import BaseModel, Field

from shared.constants import PROTOCOL_VERSION, MessageType


class BaseMessage(BaseModel):
    """Base for all JSON messages."""

    type: str
    timestamp: float = Field(default_factory=time.time)
    protocol_version: str = PROTOCOL_VERSION


# --- Client -> Backend ---


class FrameMetadata(BaseMessage):
    """Metadata sent before a binary JPEG frame."""

    type: str = MessageType.FRAME
    sequence: int
    width: int
    height: int
    jpeg_quality: int
    capture_timestamp: float
    byte_length: int


class ClientStatus(BaseMessage):
    """Periodic status update from client."""

    type: str = MessageType.CLIENT_STATUS
    airplay_running: bool = False
    airplay_pid: int | None = None
    esp32_reachable: bool = False
    esp32_ble_connected: bool | None = None
    frames_captured: int = 0
    frames_sent: int = 0
    uptime_seconds: float = 0.0


# --- Backend -> Client ---


class HIDCommandMessage(BaseMessage):
    """HID command to be forwarded to ESP32."""

    type: str = MessageType.HID_COMMAND
    action: str
    params: dict[str, int | float] = {}
    request_id: str | None = None


class ConfigUpdate(BaseMessage):
    """Backend can adjust client capture parameters dynamically."""

    type: str = MessageType.CONFIG_UPDATE
    jpeg_quality: int | None = None
    max_dimension: int | None = None
    frame_interval_ms: int | None = None


# --- Bidirectional ---


class HeartbeatPing(BaseMessage):
    type: str = MessageType.PING


class HeartbeatPong(BaseMessage):
    type: str = MessageType.PONG


class ErrorMessage(BaseMessage):
    type: str = MessageType.ERROR
    code: str
    detail: str


# --- Message Dispatcher ---

_TYPE_MAP: dict[str, type[BaseMessage]] = {
    MessageType.FRAME: FrameMetadata,
    MessageType.CLIENT_STATUS: ClientStatus,
    MessageType.HID_COMMAND: HIDCommandMessage,
    MessageType.CONFIG_UPDATE: ConfigUpdate,
    MessageType.PING: HeartbeatPing,
    MessageType.PONG: HeartbeatPong,
    MessageType.ERROR: ErrorMessage,
}


def parse_message(raw_json: str) -> BaseMessage:
    """Parse a JSON string into the correct message subtype (single parse)."""
    data = json.loads(raw_json)
    msg_type = data.get("type")
    model_class = _TYPE_MAP.get(msg_type, BaseMessage)
    return model_class.model_validate(data)
