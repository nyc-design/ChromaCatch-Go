"""Tests for the backend FastAPI REST endpoints."""

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.main import app, session_manager
from backend.session_manager import ClientSession


class TestHealthEndpoint:
    def test_health(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["role"] == "backend"


class TestStatusEndpoint:
    def test_status_no_clients(self):
        client = TestClient(app)
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert data["total_clients"] == 0
        assert data["connected_clients"] == []


class TestCommandEndpoint:
    def test_command_no_clients_broadcast(self):
        """Broadcasting to zero clients should succeed (no-op)."""
        client = TestClient(app)
        response = client.post("/command", json={"action": "click", "params": {"x": 0, "y": 0}})
        assert response.status_code == 200
        assert response.json()["status"] == "sent"

    def test_command_unknown_client(self):
        client = TestClient(app)
        response = client.post(
            "/command",
            json={"action": "click", "client_id": "nonexistent", "params": {}},
        )
        assert response.status_code == 404


class TestClientStatusEndpoint:
    def test_client_not_found(self):
        client = TestClient(app)
        response = client.get("/clients/unknown/status")
        assert response.status_code == 404

    def test_client_no_status_yet(self):
        """If client is registered but no status received, return info message."""
        ws = AsyncMock()
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            session_manager.register("test-status", ws)
        )
        try:
            client = TestClient(app)
            response = client.get("/clients/test-status/status")
            assert response.status_code == 200
            assert response.json()["detail"] == "No status received yet"
        finally:
            asyncio.get_event_loop().run_until_complete(
                session_manager.unregister("test-status")
            )


class TestClientFrameEndpoint:
    def test_frame_not_found(self):
        client = TestClient(app)
        response = client.get("/clients/unknown/frame")
        assert response.status_code == 404

    def test_frame_no_frame_available(self):
        ws = AsyncMock()
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            session_manager.register("test-frame", ws)
        )
        try:
            client = TestClient(app)
            response = client.get("/clients/test-frame/frame")
            assert response.status_code == 404
            assert "No frame" in response.json()["detail"]
        finally:
            asyncio.get_event_loop().run_until_complete(
                session_manager.unregister("test-frame")
            )

    def test_frame_returns_jpeg(self):
        ws = AsyncMock()
        import asyncio
        loop = asyncio.get_event_loop()
        session = loop.run_until_complete(
            session_manager.register("test-frame-jpeg", ws)
        )
        session.latest_frame = np.zeros((100, 100, 3), dtype=np.uint8)
        try:
            client = TestClient(app)
            response = client.get("/clients/test-frame-jpeg/frame")
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/jpeg"
            assert len(response.content) > 0
        finally:
            loop.run_until_complete(
                session_manager.unregister("test-frame-jpeg")
            )


class TestStreamEndpoint:
    def test_stream_client_not_found(self):
        client = TestClient(app)
        response = client.get("/stream/unknown")
        assert response.status_code == 404



class TestDashboardEndpoint:
    def test_dashboard_returns_html(self):
        client = TestClient(app)
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "ChromaCatch-Go" in response.text
