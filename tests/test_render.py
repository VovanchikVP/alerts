from __future__ import annotations

from alerts_bot.models import Action, AlertIn, AlertStatus, Severity
from alerts_bot.render import alert_keyboard, render_alert, render_alert_list


async def test_render_escapes_html_and_shows_meta(storage):
    alert, _ = await storage.upsert_alert(
        AlertIn(
            title="<script>alert(1)</script>",
            message="нагрузка > 90%",
            severity=Severity.CRITICAL,
            source="web-01",
            labels={"env": "prod"},
        )
    )
    text = render_alert(alert)

    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "нагрузка &gt; 90%" in text
    assert "🚨" in text and "CRITICAL" in text and "web-01" in text


async def test_keyboard_callback_data(storage):
    alert, _ = await storage.upsert_alert(
        AlertIn(
            title="Диск",
            actions=[
                Action(id="cleanup", title="Почистить"),
                Action(id="restart", title="Перезапуск", confirm=True),
            ],
        )
    )
    data = [b.callback_data for row in alert_keyboard(alert).inline_keyboard for b in row]

    assert data == [
        f"run:{alert.id}:0",
        f"ask:{alert.id}:1",  # confirm=True -> сначала подтверждение
        f"ack:{alert.id}",
        f"res:{alert.id}",
    ]
    assert all(len(item.encode()) <= 64 for item in data)


async def test_resolved_alert_has_no_control_buttons(storage):
    alert, _ = await storage.upsert_alert(AlertIn(title="Диск"))
    await storage.set_status(alert.id, AlertStatus.RESOLVED, "@oncall")
    updated = await storage.get_alert(alert.id)

    assert updated.status is AlertStatus.RESOLVED
    assert alert_keyboard(updated).inline_keyboard == []
    assert "🟢 закрыт" in render_alert(updated)


async def test_alert_list(storage):
    assert "Открытых алертов нет" in render_alert_list([])

    alert, _ = await storage.upsert_alert(AlertIn(title="Диск", severity=Severity.WARNING))
    assert f"#{alert.id}" in render_alert_list([alert])
