from __future__ import annotations

from fastapi.testclient import TestClient

from alerts_bot.api import create_app

PAYLOAD = {
    "title": "Диск заполнен",
    "severity": "error",
    "source": "web-01",
    "dedup_key": "disk:web-01",
    "actions": [{"id": "cleanup", "title": "Почистить", "confirm": True}],
}


async def test_ingest_requires_api_key(settings, storage, notifier):
    with TestClient(create_app(settings, storage, notifier)) as client:
        assert client.post("/api/alerts", json=PAYLOAD).status_code == 401
        assert (
            client.post("/api/alerts", json=PAYLOAD, headers={"X-API-Key": "wrong"}).status_code
            == 401
        )


async def test_ingest_creates_then_dedups(settings, storage, notifier):
    headers = {"X-API-Key": settings.ingest_api_key}
    with TestClient(create_app(settings, storage, notifier)) as client:
        first = client.post("/api/alerts", json=PAYLOAD, headers=headers).json()
        assert first["created"] is True
        assert first["delivered_to"] == [1]

        second = client.post("/api/alerts", json=PAYLOAD, headers=headers).json()
        assert second["created"] is False
        assert second["id"] == first["id"]

        resolved = client.post(f"/api/alerts/{first['id']}/resolve", headers=headers).json()
        assert resolved["status"] == "resolved"
        assert notifier.refreshed == [first["id"]]


async def test_ingest_rejects_broken_action(settings, storage, notifier):
    headers = {"X-API-Key": settings.ingest_api_key}
    bad = PAYLOAD | {"actions": [{"id": "плохой id", "title": "x"}]}
    with TestClient(create_app(settings, storage, notifier)) as client:
        assert client.post("/api/alerts", json=bad, headers=headers).status_code == 422


async def test_healthz(settings, storage, notifier):
    with TestClient(create_app(settings, storage, notifier)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
