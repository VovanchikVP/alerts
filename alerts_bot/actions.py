from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from .config import Settings
from .models import Action
from .storage import Alert

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Actor:
    chat_id: int
    user_id: int | None
    username: str | None

    def as_dict(self) -> dict[str, object]:
        return {"chat_id": self.chat_id, "user_id": self.user_id, "username": self.username}


@dataclass(slots=True)
class ActionResult:
    ok: bool
    detail: str

    @property
    def mark(self) -> str:
        return "✅" if self.ok else "❌"


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 тела запроса — сервер проверяет, что команда пришла от бота."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class ActionDispatcher:
    """Отправляет на сервер действие, выбранное оператором в Telegram."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    def target_url(self, alert: Alert, action: Action) -> str | None:
        if action.url:
            return str(action.url)
        return alert.callback_url or self._settings.default_callback_url

    async def execute(self, alert: Alert, action: Action, actor: Actor) -> ActionResult:
        url = self.target_url(alert, action)
        if not url:
            return ActionResult(False, "не задан callback_url для действия")

        body = {
            "alert_id": alert.id,
            "dedup_key": alert.dedup_key,
            "source": alert.source,
            "labels": alert.labels,
            "action": {"id": action.id, "title": action.title, "payload": action.payload},
            "actor": actor.as_dict(),
            "sent_at": datetime.now(UTC).isoformat(),
        }
        raw = json.dumps(body, ensure_ascii=False).encode()

        headers = {"Content-Type": "application/json"}
        if self._settings.outbound_token:
            headers["Authorization"] = f"Bearer {self._settings.outbound_token}"
        if self._settings.outbound_secret:
            headers["X-Signature"] = sign(self._settings.outbound_secret, raw)

        try:
            response = await self._client.request(
                action.method,
                url,
                content=raw,
                headers=headers,
                timeout=self._settings.outbound_timeout,
            )
        except httpx.HTTPError as exc:
            log.warning("действие %s для алерта %s не доставлено: %s", action.id, alert.id, exc)
            return ActionResult(False, f"сервер недоступен: {type(exc).__name__}")

        detail = (response.text or "").strip().replace("\n", " ")[:200]
        if response.is_success:
            return ActionResult(True, detail or f"HTTP {response.status_code}")
        return ActionResult(
            False,
            f"HTTP {response.status_code}: {detail}" if detail else f"HTTP {response.status_code}",
        )
