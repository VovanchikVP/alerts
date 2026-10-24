from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .models import AlertStatus
from .storage import Alert

STATUS_LABEL = {
    AlertStatus.FIRING: "🔴 активен",
    AlertStatus.ACKNOWLEDGED: "🟡 принят в работу",
    AlertStatus.RESOLVED: "🟢 закрыт",
}


def _human_time(value: str) -> str:
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().strftime("%d.%m %H:%M:%S")


def render_alert(alert: Alert, footer: str | None = None) -> str:
    lines = [f"{alert.severity.emoji} <b>{escape(alert.title)}</b>"]

    meta = [alert.severity.value.upper(), STATUS_LABEL[alert.status]]
    if alert.source:
        meta.insert(0, escape(alert.source))
    lines.append(" · ".join(meta))

    if alert.message:
        lines.append("")
        lines.append(escape(alert.message))

    if alert.labels:
        lines.append("")
        lines.append(
            "\n".join(f"<code>{escape(k)}</code>: {escape(v)}" for k, v in alert.labels.items())
        )

    lines.append("")
    tail = f"🕒 {_human_time(alert.started_at)} · #{alert.id}"
    if alert.actor and alert.status is not AlertStatus.FIRING:
        tail += f" · {escape(alert.actor)}"
    lines.append(tail)

    if footer:
        lines.append("")
        lines.append(footer)

    return "\n".join(lines)


def alert_keyboard(alert: Alert) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, action in enumerate(alert.actions):
        prefix = "ask" if action.confirm else "run"
        builder.button(text=action.title, callback_data=f"{prefix}:{alert.id}:{index}")
    builder.adjust(2)

    control = InlineKeyboardBuilder()
    if alert.status is AlertStatus.FIRING:
        control.button(text="👌 Принять", callback_data=f"ack:{alert.id}")
    if alert.status is not AlertStatus.RESOLVED:
        control.button(text="✅ Закрыть", callback_data=f"res:{alert.id}")
    control.adjust(2)

    builder.attach(control)
    return builder.as_markup()


def confirm_keyboard(alert_id: int, index: int, title: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Да, {title}".strip()[:48], callback_data=f"run:{alert_id}:{index}"
                ),
                InlineKeyboardButton(text="Отмена", callback_data=f"back:{alert_id}"),
            ]
        ]
    )


def render_alert_list(alerts: list[Alert]) -> str:
    if not alerts:
        return "Открытых алертов нет 🎉"
    rows = [
        f"{a.severity.emoji} <b>#{a.id}</b> {escape(a.title)}"
        f" — {STATUS_LABEL[a.status]} ({_human_time(a.started_at)})"
        for a in alerts
    ]
    return "Открытые алерты:\n\n" + "\n".join(rows)
