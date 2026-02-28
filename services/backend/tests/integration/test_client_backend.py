"""Integration test: full round-trip client <-> backend over WebSocket."""

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app, session_manager
from shared.frame_codec import encode_frame
from shared.messages import ClientStatus, FrameMetadata, HeartbeatPing, HIDCommandMessage


class TestClientBackendRoundTrip:
    """Tests the full data flow through WebSocket:
    1. Client connects
    2. Client sends frames → backend receives and stores them
    3. Backend sends commands → client receives them
    4. Client sends status → backend receives and stores it
    """

    def test_full_frame_roundtrip(self):
        """Client sends a frame, backend stores it, REST endpoint serves it."""
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            # Establish connection
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Create and send a test frame (red 50x50 image)
            frame = np.zeros((50, 50, 3), dtype=np.uint8)
            frame[:, :, 2] = 255  # Red in BGR
            jpeg_bytes, w, h = encode_frame(frame, quality=90, max_dimension=0)

            metadata = FrameMetadata(
                sequence=1, width=w, height=h,
                jpeg_quality=90, capture_timestamp=1000.0,
                byte_length=len(jpeg_bytes),
            )
            ws.send_text(metadata.model_dump_json())
            ws.send_bytes(jpeg_bytes)

            # Flush to ensure processing
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Verify frame was stored
            client_id = session_manager.connected_clients[0]
            session = session_manager.get_session(client_id)
            assert session is not None
            assert session.frames_received == 1
            assert session.latest_frame is not None
            assert session.latest_frame.shape == (50, 50, 3)

            # Verify frame is accessible via REST
            response = client.get(f"/clients/{client_id}/frame")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/jpeg"

    def test_status_roundtrip(self):
        """Client sends status, backend stores it, REST endpoint serves it."""
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Send status
            status = ClientStatus(
                airplay_running=True,
                airplay_pid=99999,
                esp32_reachable=True,
                esp32_ble_connected=True,
                frames_captured=42,
                frames_sent=40,
                uptime_seconds=300.0,
            )
            ws.send_text(status.model_dump_json())

            # Flush
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Verify via REST
            client_id = session_manager.connected_clients[0]
            response = client.get(f"/clients/{client_id}/status")
            assert response.status_code == 200
            data = response.json()
            assert data["airplay_running"] is True
            assert data["frames_captured"] == 42

    def test_command_dispatch_to_client(self):
        """Backend sends a command via REST, client receives it over WebSocket."""
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            client_id = session_manager.connected_clients[0]

            # Send command via REST
            response = client.post("/command", json={
                "client_id": client_id,
                "action": "click",
                "params": {"x": 500, "y": 800},
            })
            assert response.status_code == 200

            # Client should receive the command
            raw = ws.receive_text()
            cmd = json.loads(raw)
            assert cmd["type"] == "hid_command"
            assert cmd["action"] == "click"
            assert cmd["params"]["x"] == 500
            assert cmd["params"]["y"] == 800

    def test_broadcast_command(self):
        """Backend broadcasts a command to all clients."""
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            # Broadcast (no client_id)
            response = client.post("/command", json={
                "action": "press",
                "params": {},
            })
            assert response.status_code == 200

            raw = ws.receive_text()
            cmd = json.loads(raw)
            assert cmd["action"] == "press"

    def test_multiple_frames(self):
        """Client sends multiple frames, backend tracks count."""
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            frame = np.zeros((20, 20, 3), dtype=np.uint8)

            for seq in range(1, 4):
                jpeg_bytes, w, h = encode_frame(frame, quality=50, max_dimension=0)
                metadata = FrameMetadata(
                    sequence=seq, width=w, height=h,
                    jpeg_quality=50, capture_timestamp=1000.0 + seq,
                    byte_length=len(jpeg_bytes),
                )
                ws.send_text(metadata.model_dump_json())
                ws.send_bytes(jpeg_bytes)

            # Flush
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()

            client_id = session_manager.connected_clients[0]
            session = session_manager.get_session(client_id)
            assert session.frames_received == 3

    def test_client_disconnect_cleanup(self):
        """After client disconnects, session is cleaned up."""
        client = TestClient(app)
        with client.websocket_connect("/ws/client") as ws:
            ws.send_text(HeartbeatPing().model_dump_json())
            ws.receive_text()
            assert len(session_manager.connected_clients) == 1

        # Outside context = disconnected
        assert len(session_manager.connected_clients) == 0
