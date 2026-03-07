import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from sniper_service.main import app, service


def setup_function() -> None:
    service.replace_watch_blocks([])
    service.clear_queue()


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["role"] == "sniper-service"


def test_set_and_get_watch_blocks():
    client = TestClient(app)

    payload = {
        "watch_blocks": [
            {
                "id": "block-a",
                "server_id": "111",
                "channel_id": "222",
                "user_ids": ["333", "444"],
                "geofence": {
                    "latitude": 37.7749,
                    "longitude": -122.4194,
                    "radius_km": 5.0,
                },
                "enabled": True,
            }
        ]
    }

    put_response = client.put("/watch-blocks", json=payload)
    assert put_response.status_code == 200

    get_response = client.get("/watch-blocks")
    assert get_response.status_code == 200
    data = get_response.json()
    assert len(data["watch_blocks"]) == 1
    assert data["watch_blocks"][0]["id"] == "block-a"


def test_queue_enqueue_and_dedupe():
    client = TestClient(app)

    first = client.post(
        "/queue/enqueue",
        json={"latitude": 37.1234567, "longitude": -122.1234567, "source": "manual"},
    )
    assert first.status_code == 200
    assert first.json()["status"] == "queued"

    duplicate = client.post(
        "/queue/enqueue",
        json={"latitude": 37.1234567, "longitude": -122.1234567, "source": "manual"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"

    queue = client.get("/queue")
    assert queue.status_code == 200
    assert queue.json()["size"] == 1


def test_dispatch_next_empty_queue_returns_404():
    client = TestClient(app)
    response = client.post("/queue/dispatch-next", json={})
    assert response.status_code == 404
    assert response.json()["detail"] == "Queue is empty"


def test_dispatch_next_uses_lifo_newest_first():
    client = TestClient(app)
    client.post("/queue/enqueue", json={"latitude": 1.0, "longitude": 2.0, "source": "manual"})
    client.post("/queue/enqueue", json={"latitude": 3.0, "longitude": 4.0, "source": "manual"})
    captured_payload = {}

    class FakeResponse:
        status_code = 200
        content = b'{"status":"sent"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "sent"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            captured_payload["json"] = kwargs.get("json", {})
            return FakeResponse()

    with patch("sniper_service.service.httpx.AsyncClient", FakeAsyncClient):
        response = client.post("/queue/dispatch-next", json={})

    assert response.status_code == 200
    data = response.json()
    sent = data["sent"]
    assert sent["latitude"] == 3.0
    assert sent["longitude"] == 4.0
    assert data["location_request"] == captured_payload["json"]


def test_dispatch_next_returns_sent_metadata():
    client = TestClient(app)
    service.enqueue_coordinate(
        latitude=11.0,
        longitude=22.0,
        source="discord",
        pokemon_name="Eevee",
        level=30,
        cp=1024,
        iv_pct=98.0,
        iv_atk=15,
        iv_def=15,
        iv_sta=14,
    )

    class FakeResponse:
        status_code = 200
        content = b'{"status":"sent"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "sent"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    with patch("sniper_service.service.httpx.AsyncClient", FakeAsyncClient):
        response = client.post("/queue/dispatch-next", json={})

    assert response.status_code == 200
    sent = response.json()["sent"]
    assert sent["pokemon_name"] == "Eevee"
    assert sent["level"] == 30
    assert sent["cp"] == 1024
    assert sent["iv_pct"] == 98.0
    assert sent["iv_atk"] == 15
    assert sent["iv_def"] == 15
    assert sent["iv_sta"] == 14


def test_watch_block_setup_sets_active_client_id_for_dispatch():
    client = TestClient(app)

    block_payload = {
        "id": "block-client",
        "server_id": "111",
        "channel_id": "222",
        "user_ids": ["333"],
        "enabled": True,
    }
    put_response = client.post("/watch-blocks?client_id=ios-client-alpha", json=block_payload)
    assert put_response.status_code == 200

    client.post("/queue/enqueue", json={"latitude": 7.0, "longitude": 8.0, "source": "manual"})
    captured_payload = {}

    class FakeResponse:
        status_code = 200
        content = b'{"status":"sent"}'

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "sent"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            captured_payload["json"] = kwargs.get("json", {})
            return FakeResponse()

    with patch("sniper_service.service.httpx.AsyncClient", FakeAsyncClient):
        dispatch_response = client.post("/queue/dispatch-next", json={})

    assert dispatch_response.status_code == 200
    assert captured_payload["json"]["client_id"] == "ios-client-alpha"
    assert dispatch_response.json()["location_request"]["client_id"] == "ios-client-alpha"


def test_expired_queue_items_are_pruned_before_dispatch():
    client = TestClient(app)
    service.enqueue_coordinate(
        latitude=10.0,
        longitude=10.0,
        source="test",
        despawn_epoch=time.time() - 10,
    )

    response = client.post("/queue/dispatch-next", json={})
    assert response.status_code == 404
    assert response.json()["detail"] == "Queue is empty"


def test_delete_watch_block_not_found():
    client = TestClient(app)
    response = client.delete("/watch-blocks/does-not-exist")
    assert response.status_code == 404
