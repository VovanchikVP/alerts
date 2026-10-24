from __future__ import annotations

import asyncio

import pytest
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.methods import GetMe

from alerts_bot.app import _build_bot, _polling_forever


class FakeDispatcher:
    """Вместо реального polling отдаёт заранее заданную последовательность исходов."""

    def __init__(self, outcomes: list[Exception | None]) -> None:
        self._outcomes = list(outcomes)
        self.calls = 0

    async def start_polling(self, bot, handle_signals: bool = True) -> None:
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if outcome is not None:
            raise outcome


@pytest.fixture
def no_sleep(monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


async def test_polling_retries_network_errors(no_sleep):
    """Недоступный Telegram не должен ронять процесс — иначе под уходит в CrashLoopBackOff."""
    network_error = TelegramNetworkError(method=GetMe(), message="Request timeout error")
    dp = FakeDispatcher([network_error, network_error, None])

    await _polling_forever(dp, bot=None)

    assert dp.calls == 3
    assert no_sleep == [5, 10]  # экспоненциальная пауза между попытками


async def test_polling_backoff_is_capped(no_sleep):
    errors = [TelegramNetworkError(method=GetMe(), message="timeout") for _ in range(8)]
    dp = FakeDispatcher([*errors, None])

    await _polling_forever(dp, bot=None)

    assert max(no_sleep) == 60


async def test_bad_token_is_not_retried(no_sleep):
    """Неверный токен — ошибка конфигурации: падаем сразу и заметно."""
    dp = FakeDispatcher([TelegramUnauthorizedError(method=GetMe(), message="Unauthorized")])

    with pytest.raises(TelegramUnauthorizedError):
        await _polling_forever(dp, bot=None)

    assert no_sleep == []


def test_proxy_is_wired_into_session(settings):
    assert _build_bot(settings).session.proxy is None

    settings.telegram_proxy = "http://proxy.local:3128"
    bot = _build_bot(settings)
    assert isinstance(bot.session, AiohttpSession)
    assert bot.session.proxy == "http://proxy.local:3128"
