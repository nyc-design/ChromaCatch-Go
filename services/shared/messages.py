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
    sent_timestamp: float | None = None
    byte_length: int


class H264FrameMetadata(BaseMessage):
    """Metadata sent before a binary H.264 Access Unit."""

    type: str = MessageType.H264_FRAME
    sequence: int
    is_keyframe: bool = False
    capture_timestamp: float
    sent_timestamp: float | None = None
    byte_length: int


class AudioChunk(BaseMessage):
    """Metadata sent before a binary PCM audio chunk."""

    type: str = MessageType.AUDIO_CHUNK
    sequence: int
    sample_rate: int
    channels: int
    sample_format: str = "s16le"
    capture_timestamp: float
    sent_timestamp: float | None = None
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
    capture_source: str = "airplay"
    source_running: bool = False
    control_channel_connected: bool = False
    transport_mode: str = "websocket"
    transport_connected: bool = False
    commands_sent: int = 0
    commands_acked: int = 0
    last_command_rtt_ms: float | None = None
    audio_enabled: bool = False
    audio_source: str | None = None
    audio_chunks_captured: int = 0
    audio_chunks_sent: int = 0
    uptime_seconds: float = 0.0
    # SRT transport stats (populated when transport_mode="srt")
    srt_rtt_ms: float | None = None
    srt_bandwidth_kbps: float | None = None
    srt_packet_loss_pct: float | None = None


# --- Backend -> Client ---


class HIDCommandMessage(BaseMessage):
    """HID command to be forwarded to ESP32."""

    type: str = MessageType.HID_COMMAND
    action: str
    params: dict[str, int | float] = {}
    request_id: str | None = None
    command_id: str | None = None
    command_sequence: int | None = None
    dispatched_at_backend: float | None = None


class CommandAck(BaseMessage):
    """Ack and timing from client after forwarding a command."""

    type: str = MessageType.COMMAND_ACK
    command_id: str
    command_sequence: int | None = None
    received_at_client: float
    forwarded_at_client: float | None = None
    completed_at_client: float
    success: bool = True
    error: str | None = None


class ConfigUpdate(BaseMessage):
    """Backend can adjust client capture parameters dynamically."""

    type: str = MessageType.CONFIG_UPDATE
    jpeg_quality: int | None = None
    max_dimension: int | None = None
    frame_interval_ms: int | None = None


class LocationUpdateMessage(BaseMessage):
    """GPS coordinates to send to the iTools dongle via iOS app."""

    type: str = MessageType.LOCATION_UPDATE
    latitude: float
    longitude: float
    altitude: float = 10.0
    speed_knots: float = 0.0
    heading: float = 0.0


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
    MessageType.H264_FRAME: H264FrameMetadata,
    MessageType.AUDIO_CHUNK: AudioChunk,
    MessageType.CLIENT_STATUS: ClientStatus,
    MessageType.HID_COMMAND: HIDCommandMessage,
    MessageType.COMMAND_ACK: CommandAck,
    MessageType.CONFIG_UPDATE: ConfigUpdate,
    MessageType.LOCATION_UPDATE: LocationUpdateMessage,
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
