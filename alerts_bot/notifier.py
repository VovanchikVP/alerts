from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from .render import alert_keyboard, render_alert
from .storage import Alert, Storage

log = logging.getLogger(__name__)


class Notifier:
    """Доставка алертов в чаты и поддержание сообщений в актуальном состоянии."""

    def __init__(self, bot: Bot, storage: Storage) -> None:
        self._bot = bot
        self._storage = storage

    async def dispatch(self, alert: Alert) -> tuple[list[int], list[int]]:
        delivered: list[int] = []
        failed: list[int] = []
        known = dict(await self._storage.list_deliveries(alert.id))

        for subscriber in await self._storage.list_subscribers():
            chat_id = subscriber.chat_id
            message_id = known.get(chat_id)
            if message_id is not None:
                ok = await self.refresh_one(alert, chat_id, message_id)
            elif subscriber.wants(alert.severity):
                ok = await self._send(alert, chat_id)
            else:
                continue
            (delivered if ok else failed).append(chat_id)

        return delivered, failed

    async def refresh(self, alert: Alert, footer: str | None = None) -> None:
        for chat_id, message_id in await self._storage.list_deliveries(alert.id):
            await self.refresh_one(alert, chat_id, message_id, footer=footer)

    async def refresh_one(
        self, alert: Alert, chat_id: int, message_id: int, footer: str | None = None
    ) -> bool:
        try:
            await self._call(
                self._bot.edit_message_text,
                chat_id=chat_id,
                message_id=message_id,
                text=render_alert(alert, footer),
                reply_markup=alert_keyboard(alert),
            )
        except TelegramBadRequest as exc:
            # «message is not modified» — нормальная ситуация при повторном алерте.
            if "not modified" in str(exc):
                return True
            log.warning("не удалось обновить сообщение в чате %s: %s", chat_id, exc)
            return False
        except TelegramForbiddenError:
            await self._drop(chat_id)
            return False
        except Exception as exc:
            log.exception("ошибка обновления сообщения в чате %s: %s", chat_id, exc)
            return False
        return True

    async def _send(self, alert: Alert, chat_id: int) -> bool:
        try:
            message = await self._call(
                self._bot.send_message,
                chat_id=chat_id,
                text=render_alert(alert),
                reply_markup=alert_keyboard(alert),
            )
        except TelegramForbiddenError:
            await self._drop(chat_id)
            return False
        except Exception as exc:
            log.exception("не удалось отправить алерт %s в чат %s: %s", alert.id, chat_id, exc)
            return False
        await self._storage.save_delivery(alert.id, chat_id, message.message_id)
        return True

    async def _call(self, method, **kwargs):
        """Один повтор при флуд-лимите Telegram."""
        try:
            return await method(**kwargs)
        except TelegramRetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            return await method(**kwargs)

    async def _drop(self, chat_id: int) -> None:
        log.info("чат %s заблокировал бота — удаляю подписку", chat_id)
        await self._storage.remove_subscriber(chat_id)
