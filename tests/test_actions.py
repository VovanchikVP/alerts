from __future__ import annotations

import json

import httpx
import pytest

from alerts_bot.actions import ActionDispatcher, Actor, sign
from alerts_bot.models import Action, AlertIn, Severity

ACTOR = Actor(chat_id=1, user_id=7, username="@oncall")


@pytest.fixture
async def alert(storage):
    payload = AlertIn(
        title="Диск заполнен",
        severity=Severity.ERROR,
        source="web-01",
        actions=[Action(id="cleanup", title="Почистить", payload={"days": 7})],
    )
    created, _ = await storage.upsert_alert(payload)
    return created


def transport(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_execute_signs_and_sends_payload(settings, alert):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text="restarted")

    async with transport(handler) as client:
        result = await ActionDispatcher(settings, client).execute(alert, alert.actions[0], ACTOR)

    assert result.ok is True and result.detail == "restarted"
    assert seen["url"] == settings.default_callback_url
    assert seen["headers"]["authorization"] == "Bearer bearer-token"
    assert seen["headers"]["x-signature"] == sign(
        settings.outbound_secret, json.dumps(seen["body"], ensure_ascii=False).encode()
    )
    assert seen["body"]["action"] == {
        "id": "cleanup",
        "title": "Почистить",
        "payload": {"days": 7},
    }
    assert seen["body"]["actor"]["username"] == "@oncall"


async def test_execute_reports_server_error(settings, alert):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="unit not found")

    async with transport(handler) as client:
        result = await ActionDispatcher(settings, client).execute(alert, alert.actions[0], ACTOR)

    assert result.ok is False
    assert "500" in result.detail and "unit not found" in result.detail


async def test_execute_reports_network_error(settings, alert):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with transport(handler) as client:
        result = await ActionDispatcher(settings, client).execute(alert, alert.actions[0], ACTOR)

    assert result.ok is False and "недоступен" in result.detail


async def test_execute_without_callback_url(settings, alert):
    settings.default_callback_url = None

    async with transport(lambda request: httpx.Response(200)) as client:
        result = await ActionDispatcher(settings, client).execute(alert, alert.actions[0], ACTOR)

    assert result.ok is False and "callback_url" in result.detail
