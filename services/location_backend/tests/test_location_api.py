"""Tests for the location service FastAPI REST + WebSocket endpoints."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from location_service.main import app, session_manager, _current_locations


class TestHealthEndpoint:
    def test_health(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["role"] == "location-service"


class TestSendLocationEndpoint:
    def test_send_location_no_clients_broadcast(self):
        """Broadcasting location to zero clients should succeed."""
        client = TestClient(app)
        response = client.post(
            "/location", json={"latitude": 33.448, "longitude": -96.789}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "sent"

    def test_send_location_unknown_client(self):
        client = TestClient(app)
        response = client.post(
            "/location",
            json={
                "client_id": "nonexistent",
                "latitude": 33.448,
                "longitude": -96.789,
            },
        )
        assert response.status_code == 404

    def test_send_location_to_client(self):
        ws = AsyncMock()
        import asyncio

        loop = asyncio.get_event_loop()
        loop.run_until_complete(session_manager.register("test-loc", ws))
        try:
            client = TestClient(app)
            response = client.post(
                "/location",
                json={
                    "client_id": "test-loc",
                    "latitude": 37.335,
                    "longitude": -122.009,
                },
            )
            assert response.status_code == 200
            assert response.json()["status"] == "sent"
            ws.send_text.assert_called_once()
        finally:
            loop.run_until_complete(session_manager.unregister("test-loc"))


class TestGetLocationEndpoint:
    def test_get_location_empty(self):
        client = TestClient(app)
        response = client.get("/location")
        assert response.status_code == 200

    def test_get_location_unknown_client(self):
        client = TestClient(app)
        response = client.get("/location?client_id=nonexistent")
        assert response.status_code == 404

    def test_send_and_get_location(self):
        ws = AsyncMock()
        import asyncio

        loop = asyncio.get_event_loop()
        loop.run_until_complete(session_manager.register("test-loc-get", ws))
        try:
            client = TestClient(app)
            client.post(
                "/location",
                json={
                    "client_id": "test-loc-get",
                    "latitude": 33.448,
                    "longitude": -96.789,
                    "altitude": 200.0,
                },
            )
            response = client.get("/location?client_id=test-loc-get")
            assert response.status_code == 200
            data = response.json()
            assert data["latitude"] == 33.448
            assert data["longitude"] == -96.789
            assert data["altitude"] == 200.0
        finally:
            loop.run_until_complete(session_manager.unregister("test-loc-get"))
            _current_locations.pop("test-loc-get", None)

    def test_location_defaults(self):
        ws = AsyncMock()
        import asyncio

        loop = asyncio.get_event_loop()
        loop.run_until_complete(session_manager.register("test-loc-def", ws))
        try:
            client = TestClient(app)
            client.post(
                "/location",
                json={
                    "client_id": "test-loc-def",
                    "latitude": 0.0,
                    "longitude": 0.0,
                },
            )
            response = client.get("/location?client_id=test-loc-def")
            data = response.json()
            assert data["altitude"] == 10.0
            assert data["speed_knots"] == 0.0
            assert data["heading"] == 0.0
        finally:
            loop.run_until_complete(session_manager.unregister("test-loc-def"))
            _current_locations.pop("test-loc-def", None)
