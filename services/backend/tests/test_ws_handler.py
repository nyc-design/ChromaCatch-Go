"""Tests for backend WebSocket handler."""

import json
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app, session_manager
from shared.frame_codec import encode_frame
from shared.messages import AudioChunk, ClientStatus, CommandAck, FrameMetadata, H264FrameMetadata, HeartbeatPing


class TestWebSocketConnection:
    def test_websocket_connect_and_health(self):
        """Test that WebSocket connection works alongside REST."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

    def test_websocket_connect_disconnect(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            # Send a ping to ensure handler is running
            ws.send_text(HeartbeatPing().model_dump_json())
            response = ws.receive_text()
            assert json.loads(response)["type"] == "pong"
            # Client should be registered
            assert len(session_manager.connected_clients) == 1
        # After disconnect, client should be unregistered
        assert len(session_manager.connected_clients) == 0

    def test_websocket_send_heartbeat_ping(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ping = HeartbeatPing()
            ws.send_text(ping.model_dump_json())
            response = ws.receive_text()
            data = json.loads(response)
            assert data["type"] == "pong"

    def test_websocket_send_client_status(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            # Send ping first to ensure connection is established
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            status = ClientStatus(
                airplay_running=True,
                esp32_reachable=True,
                esp32_ble_connected=True,
                frames_captured=50,
            )
            ws.send_text(status.model_dump_json())

            # Send another ping to flush - ensures status was processed
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            client_id = session_manager.connected_clients[0]
            session = session_manager.get_session(client_id)
            assert session is not None
            assert session.last_status is not None
            assert session.last_status.airplay_running is True

    def test_websocket_send_frame(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            # Ensure connection is established
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Create a test frame
            frame = np.zeros((100, 100, 3), dtype=np.uint8)
            jpeg_bytes, w, h = encode_frame(frame, quality=70, max_dimension=0)

            # Send metadata then binary
            metadata = FrameMetadata(
                sequence=1, width=w, height=h,
                jpeg_quality=70, capture_timestamp=1000.0,
                byte_length=len(jpeg_bytes),
            )
            ws.send_text(metadata.model_dump_json())
            ws.send_bytes(jpeg_bytes)

            # Send a ping to flush processing
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            client_id = session_manager.connected_clients[0]
            session = session_manager.get_session(client_id)
            assert session is not None
            assert session.frames_received == 1
            assert session.latest_frame is not None
            assert session.latest_frame.shape == (100, 100, 3)

    def test_websocket_binary_without_metadata_ignored(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Send binary without preceding metadata
            ws.send_bytes(b"random data")

            # Should still be connected - send ping to verify
            ws.send_text(HeartbeatPing().model_dump_json())
            response = ws.receive_text()
            assert json.loads(response)["type"] == "pong"

            # No frame should have been stored
            client_id = session_manager.connected_clients[0]
            session = session_manager.get_session(client_id)
            assert session.frames_received == 0

    def test_websocket_invalid_json_ignored(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Send invalid JSON
            ws.send_text("not valid json {{{")

            # Should still be connected
            ws.send_text(HeartbeatPing().model_dump_json())
            response = ws.receive_text()
            assert json.loads(response)["type"] == "pong"

    def test_websocket_send_audio_chunk(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            raw_audio = b"\x00\x01" * 2048
            metadata = AudioChunk(
                sequence=1,
                sample_rate=44100,
                channels=2,
                capture_timestamp=1000.0,
                byte_length=len(raw_audio),
            )
            ws.send_text(metadata.model_dump_json())
            ws.send_bytes(raw_audio)

            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            client_id = session_manager.connected_clients[0]
            session = session_manager.get_session(client_id)
            assert session is not None
            assert session.audio_chunks_received == 1
            assert session.latest_audio_chunk == raw_audio

    def test_websocket_send_h264_frame(self):
        """Test H.264 Access Unit received and decoded by backend."""
        # Generate a minimal valid H.264 keyframe
        import av
        from fractions import Fraction

        codec = av.CodecContext.create("h264", "w")
        codec.width = 64
        codec.height = 64
        codec.pix_fmt = "yuv420p"
        codec.time_base = Fraction(1, 30)
        codec.options = {"preset": "ultrafast", "tune": "zerolatency"}
        codec.open()

        frame = av.VideoFrame(64, 64, "yuv420p")
        for i, plane in enumerate(frame.planes):
            if i == 0:
                plane.update(bytes(plane.buffer_size))
            else:
                plane.update(bytes([128]) * plane.buffer_size)
        frame.pts = 0

        packets = codec.encode(frame)
        packets += codec.encode(None)
        h264_data = b""
        for pkt in packets:
            h264_data += bytes(pkt)

        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            metadata = H264FrameMetadata(
                sequence=1,
                is_keyframe=True,
                capture_timestamp=1000.0,
                byte_length=len(h264_data),
            )
            ws.send_text(metadata.model_dump_json())
            ws.send_bytes(h264_data)

            # Flush processing
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            client_id = session_manager.connected_clients[0]
            session = session_manager.get_session(client_id)
            assert session is not None
            assert session.frames_received == 1
            assert session.latest_frame is not None
            assert session.latest_frame.shape == (64, 64, 3)
            # Dashboard JPEG should also be generated
            assert session.latest_frame_jpeg is not None
            assert len(session.latest_frame_jpeg) > 0

    def test_websocket_control_channel_receives_ack(self):
        client = TestClient(app)
        with client.websocket_connect("/ws/client?client_id=test-client") as frame_ws:
            frame_ws.send_text(HeartbeatPing().model_dump_json())
            frame_ws.receive_text()
            client_id = session_manager.connected_clients[0]

            session = session_manager.get_session(client_id)
            assert session is not None
            session.pending_commands["cmd-1"] = 1.0

            with client.websocket_connect("/ws/control?client_id=test-client") as control_ws:
                control_ws.send_text(
                    CommandAck(
                        command_id="cmd-1",
                        command_sequence=1,
                        received_at_client=2.0,
                        completed_at_client=3.0,
                        success=True,
                    ).model_dump_json()
                )
                control_ws.send_text(HeartbeatPing().model_dump_json())
                response = control_ws.receive_text()
                assert json.loads(response)["type"] == "pong"

                session = session_manager.get_session(client_id)
                assert session is not None
                assert session.commands_acked == 1
