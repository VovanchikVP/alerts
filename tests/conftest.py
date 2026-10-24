from __future__ import annotations

import pytest

from alerts_bot.config import Settings
from alerts_bot.storage import Storage


@pytest.fixture
async def storage(tmp_path):
    store = Storage(str(tmp_path / "test.db"))
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        bot_token="123:TEST",
        ingest_api_key="secret-key",
        db_path=str(tmp_path / "test.db"),
        allowed_chat_ids=[1, 2],
        outbound_secret="hmac-secret",
        outbound_token="bearer-token",
        default_callback_url="https://ops.example.com/hook",
    )


class FakeNotifier:
    """Подменяет Telegram: запоминает, что было бы отправлено."""

    def __init__(self) -> None:
        self.dispatched: list[int] = []
        self.refreshed: list[int] = []

    async def dispatch(self, alert):
        self.dispatched.append(alert.id)
        return [1], []

    async def refresh(self, alert, footer=None):
        self.refreshed.append(alert.id)


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()
