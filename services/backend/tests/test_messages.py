"""Tests for shared message protocol definitions."""

import json

from shared.constants import PROTOCOL_VERSION, MessageType
from shared.messages import BaseMessage, ClientStatus, ConfigUpdate, ErrorMessage, FrameMetadata, HeartbeatPing, HeartbeatPong, HIDCommandMessage


class TestBaseMessage:
    def test_has_timestamp(self):
        msg = BaseMessage(type="test")
        assert msg.timestamp > 0

    def test_has_protocol_version(self):
        msg = BaseMessage(type="test")
        assert msg.protocol_version == PROTOCOL_VERSION

    def test_roundtrip_json(self):
        msg = BaseMessage(type="test")
        raw = msg.model_dump_json()
        parsed = BaseMessage.model_validate_json(raw)
        assert parsed.type == "test"
        assert parsed.protocol_version == PROTOCOL_VERSION


class TestFrameMetadata:
    def test_creation(self):
        msg = FrameMetadata(
            sequence=1, width=960, height=540,
            jpeg_quality=70, capture_timestamp=1000.0, byte_length=45000,
        )
        assert msg.type == MessageType.FRAME
        assert msg.sequence == 1
        assert msg.width == 960
        assert msg.byte_length == 45000

    def test_roundtrip_json(self):
        msg = FrameMetadata(
            sequence=42, width=1920, height=1080,
            jpeg_quality=85, capture_timestamp=1234.5, byte_length=150000,
        )
        raw = msg.model_dump_json()
        parsed = FrameMetadata.model_validate_json(raw)
        assert parsed.sequence == 42
        assert parsed.width == 1920
        assert parsed.capture_timestamp == 1234.5

    def test_type_field_from_json(self):
        raw = FrameMetadata(
            sequence=1, width=10, height=10,
            jpeg_quality=70, capture_timestamp=0, byte_length=100,
        ).model_dump_json()
        data = json.loads(raw)
        assert data["type"] == "frame"


class TestHIDCommandMessage:
    def test_move_command(self):
        msg = HIDCommandMessage(action="move", params={"dx": 10, "dy": -5})
        assert msg.type == MessageType.HID_COMMAND
        assert msg.action == "move"
        assert msg.params["dx"] == 10

    def test_click_command(self):
        msg = HIDCommandMessage(action="click", params={"x": 100, "y": 200})
        assert msg.params == {"x": 100, "y": 200}

    def test_swipe_command(self):
        msg = HIDCommandMessage(
            action="swipe",
            params={"x1": 0, "y1": 0, "x2": 100, "y2": 200, "duration_ms": 300},
        )
        assert msg.params["duration_ms"] == 300

    def test_with_request_id(self):
        msg = HIDCommandMessage(action="click", params={"x": 0, "y": 0}, request_id="abc-123")
        parsed = HIDCommandMessage.model_validate_json(msg.model_dump_json())
        assert parsed.request_id == "abc-123"

    def test_empty_params(self):
        msg = HIDCommandMessage(action="press")
        assert msg.params == {}

    def test_roundtrip_json(self):
        msg = HIDCommandMessage(action="swipe", params={"x1": 1, "y1": 2, "x2": 3, "y2": 4})
        parsed = HIDCommandMessage.model_validate_json(msg.model_dump_json())
        assert parsed.action == "swipe"
        assert parsed.params == {"x1": 1, "y1": 2, "x2": 3, "y2": 4}


class TestClientStatus:
    def test_creation(self):
        msg = ClientStatus(
            airplay_running=True, airplay_pid=12345,
            esp32_reachable=True, esp32_ble_connected=True,
            frames_captured=100, frames_sent=95, uptime_seconds=60.0,
        )
        assert msg.type == MessageType.CLIENT_STATUS
        assert msg.airplay_running is True
        assert msg.frames_sent == 95

    def test_optional_fields(self):
        msg = ClientStatus(airplay_running=False, esp32_reachable=False)
        assert msg.airplay_pid is None
        assert msg.esp32_ble_connected is None
        assert msg.frames_captured == 0

    def test_roundtrip_json(self):
        msg = ClientStatus(airplay_running=True, esp32_reachable=False, uptime_seconds=120.5)
        parsed = ClientStatus.model_validate_json(msg.model_dump_json())
        assert parsed.uptime_seconds == 120.5


class TestConfigUpdate:
    def test_partial_update(self):
        msg = ConfigUpdate(jpeg_quality=50)
        assert msg.jpeg_quality == 50
        assert msg.max_dimension is None
        assert msg.frame_interval_ms is None

    def test_full_update(self):
        msg = ConfigUpdate(jpeg_quality=80, max_dimension=480, frame_interval_ms=100)
        assert msg.jpeg_quality == 80
        assert msg.max_dimension == 480

    def test_roundtrip_json(self):
        msg = ConfigUpdate(frame_interval_ms=500)
        parsed = ConfigUpdate.model_validate_json(msg.model_dump_json())
        assert parsed.frame_interval_ms == 500


class TestHeartbeat:
    def test_ping(self):
        msg = HeartbeatPing()
        assert msg.type == MessageType.PING

    def test_pong(self):
        msg = HeartbeatPong()
        assert msg.type == MessageType.PONG


class TestErrorMessage:
    def test_creation(self):
        msg = ErrorMessage(code="ESP32_UNREACHABLE", detail="Connection refused")
        assert msg.type == MessageType.ERROR
        assert msg.code == "ESP32_UNREACHABLE"

    def test_roundtrip_json(self):
        msg = ErrorMessage(code="FRAME_TOO_LARGE", detail="500KB limit exceeded")
        parsed = ErrorMessage.model_validate_json(msg.model_dump_json())
        assert parsed.code == "FRAME_TOO_LARGE"
