"""Tests for shared message protocol definitions."""

import json

from shared.constants import PROTOCOL_VERSION, MessageType
from shared.messages import (
    AudioChunk,
    BaseMessage,
    ClientStatus,
    CommandAck,
    ConfigUpdate,
    ErrorMessage,
    FrameMetadata,
    H264FrameMetadata,
    HeartbeatPing,
    HeartbeatPong,
    HIDCommandMessage,
    LocationUpdateMessage,
    SetHIDModeMessage,
    parse_message,
)


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
        assert parsed.sent_timestamp is None

    def test_type_field_from_json(self):
        raw = FrameMetadata(
            sequence=1, width=10, height=10,
            jpeg_quality=70, capture_timestamp=0, byte_length=100,
        ).model_dump_json()
        data = json.loads(raw)
        assert data["type"] == "frame"


class TestAudioChunk:
    def test_creation(self):
        msg = AudioChunk(
            sequence=1,
            sample_rate=44100,
            channels=2,
            capture_timestamp=1000.0,
            byte_length=4096,
        )
        assert msg.type == MessageType.AUDIO_CHUNK
        assert msg.sample_format == "s16le"

    def test_roundtrip_json(self):
        msg = AudioChunk(
            sequence=2,
            sample_rate=48000,
            channels=1,
            capture_timestamp=1.23,
            sent_timestamp=2.34,
            byte_length=1024,
        )
        parsed = AudioChunk.model_validate_json(msg.model_dump_json())
        assert parsed.sample_rate == 48000
        assert parsed.sent_timestamp == 2.34


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
        assert msg.audio_source is None

    def test_transport_fields_default(self):
        msg = ClientStatus(airplay_running=False, esp32_reachable=False)
        assert msg.transport_mode == "websocket"
        assert msg.transport_connected is False

    def test_transport_fields_srt(self):
        msg = ClientStatus(
            airplay_running=True, esp32_reachable=True,
            transport_mode="srt", transport_connected=True,
        )
        assert msg.transport_mode == "srt"
        assert msg.transport_connected is True

    def test_roundtrip_json(self):
        msg = ClientStatus(airplay_running=True, esp32_reachable=False, uptime_seconds=120.5)
        parsed = ClientStatus.model_validate_json(msg.model_dump_json())
        assert parsed.uptime_seconds == 120.5

    def test_roundtrip_json_with_transport(self):
        msg = ClientStatus(
            airplay_running=True, esp32_reachable=False,
            transport_mode="srt", transport_connected=True,
        )
        parsed = ClientStatus.model_validate_json(msg.model_dump_json())
        assert parsed.transport_mode == "srt"
        assert parsed.transport_connected is True

    def test_srt_stats_defaults(self):
        msg = ClientStatus(airplay_running=False, esp32_reachable=False)
        assert msg.srt_rtt_ms is None
        assert msg.srt_bandwidth_kbps is None
        assert msg.srt_packet_loss_pct is None

    def test_srt_stats_roundtrip(self):
        msg = ClientStatus(
            airplay_running=True, esp32_reachable=True,
            transport_mode="srt", transport_connected=True,
            srt_rtt_ms=12.5, srt_bandwidth_kbps=2500.0, srt_packet_loss_pct=0.1,
        )
        parsed = ClientStatus.model_validate_json(msg.model_dump_json())
        assert parsed.srt_rtt_ms == 12.5
        assert parsed.srt_bandwidth_kbps == 2500.0
        assert parsed.srt_packet_loss_pct == 0.1


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


class TestH264FrameMetadata:
    def test_creation(self):
        msg = H264FrameMetadata(
            sequence=1,
            is_keyframe=True,
            capture_timestamp=1000.0,
            byte_length=32000,
        )
        assert msg.type == MessageType.H264_FRAME
        assert msg.sequence == 1
        assert msg.is_keyframe is True
        assert msg.byte_length == 32000

    def test_defaults(self):
        msg = H264FrameMetadata(
            sequence=1, capture_timestamp=1000.0, byte_length=100
        )
        assert msg.is_keyframe is False
        assert msg.sent_timestamp is None

    def test_roundtrip_json(self):
        msg = H264FrameMetadata(
            sequence=42,
            is_keyframe=True,
            capture_timestamp=1234.5,
            sent_timestamp=1234.6,
            byte_length=50000,
        )
        parsed = H264FrameMetadata.model_validate_json(msg.model_dump_json())
        assert parsed.sequence == 42
        assert parsed.is_keyframe is True
        assert parsed.capture_timestamp == 1234.5
        assert parsed.sent_timestamp == 1234.6

    def test_type_field_from_json(self):
        raw = H264FrameMetadata(
            sequence=1, capture_timestamp=0, byte_length=100
        ).model_dump_json()
        import json
        data = json.loads(raw)
        assert data["type"] == "h264_frame"

    def test_parse_message_dispatch(self):
        msg = H264FrameMetadata(
            sequence=5, is_keyframe=True, capture_timestamp=1.0, byte_length=500
        )
        parsed = parse_message(msg.model_dump_json())
        assert isinstance(parsed, H264FrameMetadata)
        assert parsed.sequence == 5
        assert parsed.is_keyframe is True


class TestLocationUpdateMessage:
    def test_creation(self):
        msg = LocationUpdateMessage(latitude=33.448, longitude=-96.789)
        assert msg.type == MessageType.LOCATION_UPDATE
        assert msg.latitude == 33.448
        assert msg.longitude == -96.789

    def test_defaults(self):
        msg = LocationUpdateMessage(latitude=0.0, longitude=0.0)
        assert msg.altitude == 10.0
        assert msg.speed_knots == 0.0
        assert msg.heading == 0.0

    def test_roundtrip_json(self):
        msg = LocationUpdateMessage(
            latitude=33.448, longitude=-96.789,
            altitude=200.0, speed_knots=4.7, heading=180.0,
        )
        parsed = LocationUpdateMessage.model_validate_json(msg.model_dump_json())
        assert parsed.latitude == 33.448
        assert parsed.longitude == -96.789
        assert parsed.altitude == 200.0
        assert parsed.speed_knots == 4.7
        assert parsed.heading == 180.0

    def test_parse_message_dispatch(self):
        msg = LocationUpdateMessage(latitude=37.335, longitude=-122.009)
        parsed = parse_message(msg.model_dump_json())
        assert isinstance(parsed, LocationUpdateMessage)
        assert parsed.latitude == 37.335


class TestSetHIDModeMessage:
    def test_creation(self):
        msg = SetHIDModeMessage(hid_mode="gamepad")
        assert msg.type == MessageType.SET_HID_MODE
        assert msg.hid_mode == "gamepad"

    def test_combo_mode(self):
        msg = SetHIDModeMessage(hid_mode="combo")
        assert msg.hid_mode == "combo"

    def test_roundtrip_json(self):
        msg = SetHIDModeMessage(hid_mode="keyboard")
        parsed = SetHIDModeMessage.model_validate_json(msg.model_dump_json())
        assert parsed.hid_mode == "keyboard"
        assert parsed.type == "set_hid_mode"

    def test_parse_message_dispatch(self):
        msg = SetHIDModeMessage(hid_mode="gamepad")
        parsed = parse_message(msg.model_dump_json())
        assert isinstance(parsed, SetHIDModeMessage)
        assert parsed.hid_mode == "gamepad"


class TestCommandAck:
    def test_roundtrip_json(self):
        msg = CommandAck(
            command_id="cmd-1",
            command_sequence=10,
            received_at_client=1000.0,
            forwarded_at_client=1000.1,
            completed_at_client=1000.2,
            success=True,
        )
        parsed = CommandAck.model_validate_json(msg.model_dump_json())
        assert parsed.command_id == "cmd-1"
        assert parsed.success is True
