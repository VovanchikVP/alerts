"""Прогон реальных хендлеров aiogram через подменённую сессию Telegram."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.methods import (
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from alerts_bot.actions import ActionDispatcher
from alerts_bot.bot import router
from alerts_bot.models import Action, AlertIn, AlertStatus, Severity
from alerts_bot.notifier import Notifier

CHAT_ID = 1
USER = User(id=7, is_bot=False, first_name="Дежурный", username="oncall")
CHAT = Chat(id=CHAT_ID, type="private")


class FakeSession:
    """Вместо похода в Telegram складывает вызовы в список."""

    def __init__(self) -> None:
        self.calls: list = []
        self._message_id = 100

    async def __call__(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage | EditMessageText | EditMessageReplyMarkup):
            self._message_id += 1
            return Message(
                message_id=self._message_id,
                date=datetime.now(UTC),
                chat=CHAT,
                text=getattr(method, "text", "") or "",
            )
        return True

    async def close(self) -> None:
        return None

    def of(self, kind) -> list:
        return [call for call in self.calls if isinstance(call, kind)]


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def bot(session) -> Bot:
    return Bot(
        token="42:TESTTESTTESTTESTTESTTESTTEST",
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


@pytest.fixture
def http_handler():
    """Мутируемый обработчик исходящих запросов к серверу."""
    state = {"response": lambda request: httpx.Response(200, text="done"), "seen": []}

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(request)
        return state["response"](request)

    state["handler"] = handler
    return state


@pytest.fixture(scope="module")
def dispatcher() -> Dispatcher:
    # Router можно присоединить только к одному Dispatcher, поэтому он один на модуль,
    # а зависимости подменяются перед каждым тестом.
    instance = Dispatcher()
    instance.include_router(router)
    return instance


@pytest.fixture
async def dp(dispatcher, bot, storage, settings, http_handler) -> Dispatcher:
    client = httpx.AsyncClient(transport=httpx.MockTransport(http_handler["handler"]))
    dispatcher.workflow_data.update(
        settings=settings,
        storage=storage,
        notifier=Notifier(bot, storage),
        action_dispatcher=ActionDispatcher(settings, client),
    )
    yield dispatcher
    await client.aclose()


def text_update(text: str, chat_id: int = CHAT_ID) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private"),
            from_user=USER,
            text=text,
        ),
    )


def button_update(data: str) -> Update:
    return Update(
        update_id=2,
        callback_query=CallbackQuery(
            id="cb-1",
            from_user=USER,
            chat_instance="ci",
            data=data,
            message=Message(message_id=500, date=datetime.now(UTC), chat=CHAT, text="alert"),
        ),
    )


async def seed_alert(storage, bot_message_id: int = 500):
    alert, _ = await storage.upsert_alert(
        AlertIn(
            title="Диск заполнен",
            severity=Severity.ERROR,
            source="web-01",
            actions=[
                Action(id="cleanup", title="Почистить", payload={"days": 7}),
                Action(id="restart", title="Перезапуск", confirm=True),
            ],
        )
    )
    await storage.add_subscriber(CHAT_ID, "ops")
    await storage.save_delivery(alert.id, CHAT_ID, bot_message_id)
    return alert


async def test_start_subscribes_chat(dp, bot, storage, session):
    await dp.feed_update(bot, text_update("/start"))

    assert await storage.get_subscriber(CHAT_ID) is not None
    assert "Подписка оформлена" in session.of(SendMessage)[0].text


async def test_start_rejected_for_unknown_chat(dp, bot, storage, session):
    await dp.feed_update(bot, text_update("/start", chat_id=999))

    assert await storage.get_subscriber(999) is None
    assert "не в списке разрешённых" in session.of(SendMessage)[0].text


async def test_button_runs_action_on_server(dp, bot, storage, session, http_handler):
    alert = await seed_alert(storage)

    await dp.feed_update(bot, button_update(f"run:{alert.id}:0"))

    request = http_handler["seen"][0]
    body = request.read().decode()
    assert "cleanup" in body and "@oncall" in body
    assert request.headers["x-signature"]

    log_entry = (await storage.recent_actions(alert.id))[0]
    assert log_entry["action_id"] == "cleanup" and log_entry["ok"] == 1
    # сообщение с алертом обновилось и показывает журнал действий
    assert "cleanup" in session.of(EditMessageText)[0].text


async def test_failed_action_is_reported(dp, bot, storage, session, http_handler):
    alert = await seed_alert(storage)
    http_handler["response"] = lambda request: httpx.Response(500, text="no such unit")

    await dp.feed_update(bot, button_update(f"run:{alert.id}:0"))

    assert (await storage.recent_actions(alert.id))[0]["ok"] == 0
    assert "не выполнено" in session.of(SendMessage)[0].text


async def test_confirm_button_asks_before_running(dp, bot, storage, session, http_handler):
    alert = await seed_alert(storage)

    await dp.feed_update(bot, button_update(f"ask:{alert.id}:1"))

    assert http_handler["seen"] == []  # на сервер пока ничего не ушло
    markup = session.of(EditMessageReplyMarkup)[0].reply_markup
    assert [b.callback_data for b in markup.inline_keyboard[0]] == [
        f"run:{alert.id}:1",
        f"back:{alert.id}",
    ]


async def test_ack_and_resolve_change_status(dp, bot, storage):
    alert = await seed_alert(storage)

    await dp.feed_update(bot, button_update(f"ack:{alert.id}"))
    acked = await storage.get_alert(alert.id)
    assert acked.status is AlertStatus.ACKNOWLEDGED and acked.actor == "@oncall"

    await dp.feed_update(bot, button_update(f"res:{alert.id}"))
    resolved = await storage.get_alert(alert.id)
    assert resolved.status is AlertStatus.RESOLVED


async def test_actions_on_resolved_alert_are_blocked(dp, bot, storage, http_handler):
    alert = await seed_alert(storage)
    await storage.set_status(alert.id, AlertStatus.RESOLVED, "server")

    await dp.feed_update(bot, button_update(f"run:{alert.id}:0"))

    assert http_handler["seen"] == []
    assert await storage.recent_actions(alert.id) == []


async def test_mute_and_level_commands(dp, bot, storage, session):
    await dp.feed_update(bot, text_update("/start"))
    await dp.feed_update(bot, text_update("/level critical"))
    await dp.feed_update(bot, text_update("/mute 2h"))

    subscriber = await storage.get_subscriber(CHAT_ID)
    assert subscriber.min_severity is Severity.CRITICAL
    assert subscriber.muted_until is not None
    assert subscriber.wants(Severity.ERROR) is False

    await dp.feed_update(bot, text_update("/unmute"))
    assert (await storage.get_subscriber(CHAT_ID)).muted_until is None


async def test_alerts_command_lists_open(dp, bot, storage, session):
    alert = await seed_alert(storage)

    await dp.feed_update(bot, text_update("/alerts"))

    assert f"#{alert.id}" in session.of(SendMessage)[-1].text
