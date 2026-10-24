from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация читается из переменных окружения или файла .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ALERTS_",
        extra="ignore",
    )

    # --- Telegram ---
    bot_token: str
    # Чаты, которым разрешено подписываться на алерты. Пустой список = разрешено всем,
    # кто знает имя бота (не рекомендуется).
    # NoDecode: иначе pydantic-settings попытается разобрать значение как JSON
    # и «-100123,987» из окружения уронит старт ещё до валидатора ниже.
    allowed_chat_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    # --- HTTP API, принимающее алерты с сервера ---
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    # Ключ, который сервер шлёт в заголовке X-API-Key.
    ingest_api_key: str

    # --- Обратный канал: действия, которые бот отправляет на сервер ---
    # URL по умолчанию; алерт может переопределить его полем callback_url.
    default_callback_url: str | None = None
    # Кладётся в Authorization: Bearer <...> исходящего запроса.
    outbound_token: str | None = None
    # Если задан, исходящий запрос подписывается HMAC-SHA256 в заголовке X-Signature.
    outbound_secret: str | None = None
    outbound_timeout: float = 10.0

    # --- Хранилище ---
    db_path: str = "alerts.db"

    @field_validator("allowed_chat_ids", mode="before")
    @classmethod
    def _split_chat_ids(cls, value: object) -> object:
        if isinstance(value, str):
            # Принимаем и «-100,987», и JSON-вид «[-100, 987]».
            raw = value.strip().strip("[]").replace(";", ",")
            return [part.strip().strip("\"'") for part in raw.split(",") if part.strip()]
        return value

    def chat_allowed(self, chat_id: int) -> bool:
        return not self.allowed_chat_ids or chat_id in self.allowed_chat_ids


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
