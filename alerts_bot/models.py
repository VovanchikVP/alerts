from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator

ACTION_ID_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,32}$")


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]

    @property
    def emoji(self) -> str:
        return _SEVERITY_EMOJI[self]


_SEVERITY_RANK = {
    Severity.INFO: 10,
    Severity.WARNING: 20,
    Severity.ERROR: 30,
    Severity.CRITICAL: 40,
}
_SEVERITY_EMOJI = {
    Severity.INFO: "ℹ️",
    Severity.WARNING: "⚠️",
    Severity.ERROR: "🔥",
    Severity.CRITICAL: "🚨",
}


class AlertStatus(StrEnum):
    FIRING = "firing"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class Action(BaseModel):
    """Действие, которое оператор может запустить на сервере кнопкой под алертом."""

    id: str
    title: str = Field(max_length=48)
    # Произвольные данные, которые сервер получит обратно вместе с действием.
    payload: dict[str, Any] = Field(default_factory=dict)
    # Спросить подтверждение перед отправкой (для restart/rollback и подобного).
    confirm: bool = False
    # Переопределение адреса/метода для конкретного действия.
    url: HttpUrl | None = None
    method: str = "POST"

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not ACTION_ID_RE.match(value):
            raise ValueError(f"недопустимый id действия: {value!r}")
        return value

    @field_validator("method")
    @classmethod
    def _check_method(cls, value: str) -> str:
        method = value.upper()
        if method not in {"POST", "PUT", "PATCH", "DELETE", "GET"}:
            raise ValueError(f"недопустимый HTTP-метод: {value!r}")
        return method


class AlertIn(BaseModel):
    """Тело запроса POST /api/alerts."""

    title: str = Field(max_length=256)
    message: str = ""
    severity: Severity = Severity.WARNING
    source: str | None = Field(default=None, max_length=128)
    # Ключ схлопывания: повторный алерт с тем же ключом обновляет уже отправленное
    # сообщение, а не плодит новые.
    dedup_key: str | None = Field(default=None, max_length=128)
    labels: dict[str, str] = Field(default_factory=dict)
    actions: list[Action] = Field(default_factory=list, max_length=8)
    callback_url: HttpUrl | None = None
    status: AlertStatus = AlertStatus.FIRING
    # Время события на стороне сервера.
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("actions")
    @classmethod
    def _unique_action_ids(cls, value: list[Action]) -> list[Action]:
        ids = [action.id for action in value]
        if len(ids) != len(set(ids)):
            raise ValueError("id действий должны быть уникальными")
        return value


class AlertOut(BaseModel):
    id: int
    dedup_key: str | None
    status: AlertStatus
    created: bool
    delivered_to: list[int]
    failed_for: list[int] = Field(default_factory=list)
