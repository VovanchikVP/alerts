from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alerts_bot.models import Action, AlertIn, AlertStatus, Severity
from alerts_bot.storage import Subscriber


def make_alert(**kwargs) -> AlertIn:
    base = {
        "title": "Диск заполнен",
        "severity": Severity.ERROR,
        "dedup_key": "disk:web-01",
        "actions": [Action(id="cleanup", title="Почистить")],
    }
    return AlertIn(**(base | kwargs))


async def test_dedup_updates_existing_alert(storage):
    first, created = await storage.upsert_alert(make_alert())
    assert created is True

    second, created = await storage.upsert_alert(make_alert(message="94%"))
    assert created is False
    assert second.id == first.id
    assert second.message == "94%"


async def test_resolved_alert_does_not_block_new_one(storage):
    first, _ = await storage.upsert_alert(make_alert())
    await storage.set_status(first.id, AlertStatus.RESOLVED, "ops")

    second, created = await storage.upsert_alert(make_alert())
    assert created is True
    assert second.id != first.id


async def test_actions_round_trip(storage):
    alert, _ = await storage.upsert_alert(
        make_alert(
            actions=[
                Action(id="restart", title="Перезапуск", confirm=True, payload={"unit": "nginx"})
            ]
        )
    )
    action = alert.action_by_index(0)
    assert action is not None
    assert action.confirm is True
    assert action.payload == {"unit": "nginx"}
    assert alert.action_by_index(5) is None


async def test_open_alerts_exclude_resolved(storage):
    alert, _ = await storage.upsert_alert(make_alert())
    await storage.upsert_alert(make_alert(dedup_key="cpu:web-02", title="CPU"))
    await storage.set_status(alert.id, AlertStatus.RESOLVED, "ops")

    open_alerts = await storage.list_open_alerts()
    assert [a.title for a in open_alerts] == ["CPU"]


async def test_subscriber_and_action_log(storage):
    await storage.add_subscriber(42, "ops")
    await storage.set_min_severity(42, Severity.ERROR)
    subscriber = await storage.get_subscriber(42)
    assert subscriber is not None and subscriber.min_severity is Severity.ERROR

    alert, _ = await storage.upsert_alert(make_alert())
    await storage.log_action(
        alert_id=alert.id,
        action_id="cleanup",
        chat_id=42,
        user_id=7,
        username="@oncall",
        ok=True,
        detail="ok",
    )
    entries = await storage.recent_actions(alert.id)
    assert entries[0]["action_id"] == "cleanup" and entries[0]["ok"] == 1

    assert await storage.remove_subscriber(42) is True
    assert await storage.remove_subscriber(42) is False


def test_subscriber_filters():
    now = datetime.now(UTC)
    muted = Subscriber(1, "ops", Severity.WARNING, now + timedelta(hours=1))

    assert muted.wants(Severity.INFO, now) is False  # ниже порога
    assert muted.wants(Severity.ERROR, now) is False  # порог ок, но тишина
    assert muted.wants(Severity.CRITICAL, now) is True  # critical пробивает тишину

    active = Subscriber(1, "ops", Severity.WARNING, None)
    assert active.wants(Severity.ERROR, now) is True
