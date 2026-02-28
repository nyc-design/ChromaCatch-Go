"""Tests for backend WebSocket handler."""

import json
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app, session_manager
from shared.frame_codec import encode_frame
from shared.messages import ClientStatus, FrameMetadata, HeartbeatPing


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
