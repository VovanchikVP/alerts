from __future__ import annotations

import pytest

from alerts_bot.config import Settings, load_settings

BASE_ENV = {
    "ALERTS_BOT_TOKEN": "42:TEST",
    "ALERTS_INGEST_API_KEY": "key",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # чтобы не подхватился .env разработчика
    for name, value in BASE_ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("-1001234567890,987654", [-1001234567890, 987654]),
        ("-100123, 987", [-100123, 987]),
        ("111;222", [111, 222]),
        ("[1, 2]", [1, 2]),
        ("", []),
    ],
)
def test_allowed_chat_ids_from_env(env, raw: str, expected: list[int]):
    """Переменные окружения — основной способ конфигурации в k8s."""
    env.setenv("ALERTS_ALLOWED_CHAT_IDS", raw)
    assert load_settings().allowed_chat_ids == expected


def test_chat_allowed_defaults_to_everyone(env):
    settings = load_settings()
    assert settings.allowed_chat_ids == []
    assert settings.chat_allowed(123) is True


def test_missing_required_env_fails(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALERTS_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ALERTS_INGEST_API_KEY", raising=False)
    with pytest.raises(ValueError):
        Settings()  # type: ignore[call-arg]
