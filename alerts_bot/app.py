from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramServerError
from aiogram.types import BotCommand

from .actions import ActionDispatcher
from .api import create_app
from .bot import router
from .config import Settings, load_settings
from .notifier import Notifier
from .storage import Storage

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="подписаться на алерты"),
    BotCommand(command="alerts", description="открытые алерты"),
    BotCommand(command="level", description="минимальная важность"),
    BotCommand(command="mute", description="тишина на время"),
    BotCommand(command="unmute", description="снять тишину"),
    BotCommand(command="stop", description="отписаться"),
    BotCommand(command="help", description="справка"),
]


async def _preflight(bot: Bot) -> None:
    """Снять webhook (иначе getUpdates отдаёт 409) и обновить меню команд."""
    try:
        async with asyncio.timeout(20):
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands(COMMANDS)
            me = await bot.get_me()
    except (TelegramAPIError, TimeoutError) as exc:
        log.warning(
            "Telegram недоступен на старте (%s: %s); polling подключится сам",
            type(exc).__name__,
            exc,
        )
    else:
        log.info("бот подключён как @%s", me.username)


async def _polling_forever(dp: Dispatcher, bot: Bot) -> None:
    """Long polling с переподключением.

    `start_polling` вызывает getMe до входа в цикл обновлений, и этот вызов не защищён
    внутренним backoff aiogram: без обёртки недоступный Telegram роняет весь процесс, а в
    k8s — уводит под в CrashLoopBackOff вместе с приёмом алертов.
    """
    delay = 5
    while True:
        try:
            await dp.start_polling(bot, handle_signals=False)
            return
        except (TelegramNetworkError, TelegramServerError) as exc:
            log.warning("polling оборвался (%s), повтор через %s с", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


def _build_bot(settings: Settings) -> Bot:
    session = AiohttpSession(proxy=settings.telegram_proxy) if settings.telegram_proxy else None
    return Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def run(settings: Settings | None = None) -> None:
    settings = settings or load_settings()

    storage = Storage(settings.db_path)
    await storage.connect()

    bot = _build_bot(settings)
    notifier = Notifier(bot, storage)
    http_client = httpx.AsyncClient(follow_redirects=True)

    dp = Dispatcher()
    dp.include_router(router)
    dp["settings"] = settings
    dp["storage"] = storage
    dp["notifier"] = notifier
    dp["action_dispatcher"] = ActionDispatcher(settings, http_client)

    api = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, storage, notifier),
            host=settings.api_host,
            port=settings.api_port,
            log_level="info",
        )
    )

    # Предстартовые вызовы Telegram — в фоне: пока сеть до api.telegram.org не поднялась,
    # HTTP-приём алертов и readiness-проба всё равно должны работать.
    preflight = asyncio.create_task(_preflight(bot), name="preflight")

    try:
        log.info(
            "API слушает %s:%s, бот в режиме long polling", settings.api_host, settings.api_port
        )

        tasks = {
            asyncio.create_task(_polling_forever(dp, bot), name="telegram"),
            asyncio.create_task(api.serve(), name="api"),
        }
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        api.should_exit = True
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for task in done:
            task.result()  # пробросить ошибку, из-за которой всё остановилось
    finally:
        preflight.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await preflight
        await http_client.aclose()
        await bot.session.close()
        await storage.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run())
