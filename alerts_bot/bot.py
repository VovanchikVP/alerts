from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from html import escape

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from .actions import ActionDispatcher, Actor
from .config import Settings
from .models import AlertStatus, Severity
from .notifier import Notifier
from .render import confirm_keyboard, render_alert, render_alert_list
from .storage import Alert, Storage

log = logging.getLogger(__name__)
router = Router(name="alerts")

DURATION_RE = re.compile(r"^(\d+)\s*([mhd])$", re.IGNORECASE)
DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days"}

HELP = (
    "Бот дежурного: принимает алерты с сервера и отправляет обратно действия.\n\n"
    "<b>Команды</b>\n"
    "/start — подписаться на алерты\n"
    "/stop — отписаться\n"
    "/alerts — открытые алерты\n"
    "/level &lt;info|warning|error|critical&gt; — минимальная важность\n"
    "/mute &lt;30m|2h|1d&gt; — тишина (critical приходит всегда)\n"
    "/unmute — снять тишину\n"
    "/id — id этого чата\n"
)


def _actor_name(query: CallbackQuery) -> str:
    user = query.from_user
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def _chat_title(message: Message) -> str:
    if message.chat.title:
        return message.chat.title
    return message.from_user.full_name if message.from_user else ""


async def _footer(storage: Storage, alert_id: int, limit: int = 3) -> str | None:
    entries = await storage.recent_actions(alert_id, limit)
    if not entries:
        return None
    rows = []
    for entry in reversed(entries):
        when = datetime.fromisoformat(entry["created_at"]).astimezone().strftime("%H:%M")
        mark = "✅" if entry["ok"] else "❌"
        who = entry["username"] or entry["user_id"] or "?"
        row = f"{mark} <code>{escape(entry['action_id'])}</code> · {escape(str(who))} · {when}"
        if entry["detail"]:
            row += f"\n   <i>{escape(entry['detail'][:120])}</i>"
        rows.append(row)
    return "<b>Действия</b>\n" + "\n".join(rows)


async def _load(
    query: CallbackQuery, storage: Storage, alert_id: int, *, require_open: bool = True
) -> Alert | None:
    alert = await storage.get_alert(alert_id)
    if alert is None:
        await query.answer("Алерт не найден", show_alert=True)
        return None
    if require_open and alert.status is AlertStatus.RESOLVED:
        await query.answer("Алерт уже закрыт", show_alert=True)
        return None
    return alert


# ---------------------------------------------------------------- команды


@router.message(CommandStart())
async def cmd_start(message: Message, storage: Storage, settings: Settings) -> None:
    if not settings.chat_allowed(message.chat.id):
        await message.answer(
            f"Этот чат не в списке разрешённых.\nchat_id: <code>{message.chat.id}</code>"
        )
        return
    await storage.add_subscriber(message.chat.id, _chat_title(message))
    await message.answer("Подписка оформлена, алерты будут приходить сюда.\n\n" + HELP)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("stop"))
async def cmd_stop(message: Message, storage: Storage) -> None:
    removed = await storage.remove_subscriber(message.chat.id)
    await message.answer("Отписка выполнена." if removed else "Чат и так не подписан.")


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    await message.answer(f"chat_id: <code>{message.chat.id}</code>")


@router.message(Command("alerts"))
async def cmd_alerts(message: Message, storage: Storage) -> None:
    await message.answer(render_alert_list(await storage.list_open_alerts()))


@router.message(Command("level"))
async def cmd_level(message: Message, command: CommandObject, storage: Storage) -> None:
    subscriber = await storage.get_subscriber(message.chat.id)
    if subscriber is None:
        await message.answer("Сначала /start.")
        return
    raw = (command.args or "").strip().lower()
    if not raw:
        await message.answer(f"Текущий минимум: <b>{subscriber.min_severity.value}</b>")
        return
    try:
        severity = Severity(raw)
    except ValueError:
        await message.answer("Допустимые значения: info, warning, error, critical")
        return
    await storage.set_min_severity(message.chat.id, severity)
    await message.answer(f"Минимальная важность: <b>{severity.value}</b>")


@router.message(Command("mute"))
async def cmd_mute(message: Message, command: CommandObject, storage: Storage) -> None:
    if await storage.get_subscriber(message.chat.id) is None:
        await message.answer("Сначала /start.")
        return
    match = DURATION_RE.match((command.args or "1h").strip())
    if not match:
        await message.answer("Формат: /mute 30m | 2h | 1d")
        return
    amount, unit = int(match.group(1)), match.group(2).lower()
    until = datetime.now(UTC) + timedelta(**{DURATION_UNITS[unit]: amount})
    await storage.set_mute(message.chat.id, until)
    await message.answer(
        f"Тишина до {until.astimezone():%d.%m %H:%M}. Алерты уровня critical всё равно придут."
    )


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, storage: Storage) -> None:
    await storage.set_mute(message.chat.id, None)
    await message.answer("Тишина снята.")


# ---------------------------------------------------------------- кнопки


@router.callback_query(F.data.startswith("ask:"))
async def cb_ask(query: CallbackQuery, storage: Storage) -> None:
    _, raw_id, raw_index = query.data.split(":")
    alert = await _load(query, storage, int(raw_id))
    if alert is None:
        return
    action = alert.action_by_index(int(raw_index))
    if action is None:
        await query.answer("Действие недоступно", show_alert=True)
        return
    await query.message.edit_reply_markup(
        reply_markup=confirm_keyboard(alert.id, int(raw_index), action.title)
    )
    await query.answer(f"Подтвердите: {action.title}")


@router.callback_query(F.data.startswith("back:"))
async def cb_back(query: CallbackQuery, storage: Storage, notifier: Notifier) -> None:
    alert = await _load(query, storage, int(query.data.split(":")[1]), require_open=False)
    if alert is None:
        return
    await notifier.refresh_one(
        alert, query.message.chat.id, query.message.message_id, await _footer(storage, alert.id)
    )
    await query.answer("Отменено")


@router.callback_query(F.data.startswith("run:"))
async def cb_run(
    query: CallbackQuery,
    storage: Storage,
    action_dispatcher: ActionDispatcher,
    notifier: Notifier,
) -> None:
    _, raw_id, raw_index = query.data.split(":")
    alert = await _load(query, storage, int(raw_id))
    if alert is None:
        return
    action = alert.action_by_index(int(raw_index))
    if action is None:
        await query.answer("Действие недоступно", show_alert=True)
        return

    await query.answer(f"Отправляю: {action.title}")
    actor = Actor(
        chat_id=query.message.chat.id,
        user_id=query.from_user.id,
        username=_actor_name(query),
    )
    result = await action_dispatcher.execute(alert, action, actor)
    await storage.log_action(
        alert_id=alert.id,
        action_id=action.id,
        chat_id=actor.chat_id,
        user_id=actor.user_id,
        username=actor.username,
        ok=result.ok,
        detail=result.detail,
    )
    log.info(
        "действие %s по алерту %s от %s: ok=%s (%s)",
        action.id,
        alert.id,
        actor.username,
        result.ok,
        result.detail,
    )
    await notifier.refresh(alert, await _footer(storage, alert.id))
    if not result.ok:
        await query.message.answer(
            f"❌ Действие <b>{escape(action.title)}</b> не выполнено: {escape(result.detail)}"
        )


@router.callback_query(F.data.startswith("ack:"))
async def cb_ack(query: CallbackQuery, storage: Storage, notifier: Notifier) -> None:
    await _change_status(query, storage, notifier, AlertStatus.ACKNOWLEDGED, "Принято в работу")


@router.callback_query(F.data.startswith("res:"))
async def cb_resolve(query: CallbackQuery, storage: Storage, notifier: Notifier) -> None:
    await _change_status(query, storage, notifier, AlertStatus.RESOLVED, "Алерт закрыт")


async def _change_status(
    query: CallbackQuery,
    storage: Storage,
    notifier: Notifier,
    status: AlertStatus,
    note: str,
) -> None:
    alert = await _load(query, storage, int(query.data.split(":")[1]))
    if alert is None:
        return
    actor = _actor_name(query)
    await storage.set_status(alert.id, status, actor)
    updated = await storage.get_alert(alert.id)
    assert updated is not None
    await notifier.refresh(updated, await _footer(storage, alert.id))
    await query.answer(note)


@router.callback_query()
async def cb_unknown(query: CallbackQuery) -> None:
    await query.answer("Кнопка устарела", show_alert=True)


class AccessMiddleware(BaseMiddleware):
    """Пускает к боту только чаты из ALERTS_ALLOWED_CHAT_IDS."""

    async def __call__(self, handler, event, data):  # type: ignore[override]
        settings: Settings = data["settings"]
        chat = getattr(event, "chat", None) or getattr(
            getattr(event, "message", None), "chat", None
        )
        if chat is None or settings.chat_allowed(chat.id):
            return await handler(event, data)
        log.warning("запрос из неразрешённого чата %s", chat.id)
        if isinstance(event, Message):
            await event.answer(
                "Этот чат не в списке разрешённых.\n"
                f"Передайте администратору chat_id: <code>{chat.id}</code>"
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("Нет доступа", show_alert=True)
        return None


router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())


__all__ = ["render_alert", "router"]
