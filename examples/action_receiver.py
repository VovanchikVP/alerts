"""Пример: сервер принимает действия, нажатые дежурным в Telegram.

uvicorn examples.action_receiver:app --port 9000
"""

from __future__ import annotations

import hashlib
import hmac
import subprocess

from fastapi import FastAPI, Header, HTTPException, Request

SECRET = "секрет-подписи"  # == ALERTS_OUTBOUND_SECRET
TOKEN = "токен-сервера"  # == ALERTS_OUTBOUND_TOKEN

app = FastAPI()


def verify(raw: bytes, signature: str | None, authorization: str | None) -> None:
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")
    expected = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "bad signature")


@app.post("/hooks/telegram-action")
async def handle(
    request: Request,
    x_signature: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    raw = await request.body()
    verify(raw, x_signature, authorization)

    body = await request.json()
    action = body["action"]["id"]
    payload = body["action"].get("payload", {})
    actor = body["actor"]["username"]
    print(f"{actor} запустил {action} по алерту #{body['alert_id']}")

    match action:
        case "cleanup_logs":
            days = payload.get("older_than_days", 7)
            result = subprocess.run(
                ["find", "/var/log", "-name", "*.gz", "-mtime", f"+{days}", "-delete"],
                capture_output=True,
                text=True,
            )
            if result.returncode:
                raise HTTPException(500, result.stderr.strip()[:200])
            return {"status": "ok", "detail": f"логи старше {days} дней удалены"}
        case "restart_nginx":
            unit = payload.get("unit", "nginx.service")
            result = subprocess.run(["systemctl", "restart", unit], capture_output=True, text=True)
            if result.returncode:
                raise HTTPException(500, result.stderr.strip()[:200])
            return {"status": "ok", "detail": f"{unit} перезапущен"}
        case _:
            raise HTTPException(400, f"неизвестное действие: {action}")


# Тело запроса от бота:
# {
#   "alert_id": 12, "dedup_key": "...", "source": "web-01.prod", "labels": {...},
#   "action": {"id": "restart_nginx", "title": "...", "payload": {...}},
#   "actor": {"chat_id": -100..., "user_id": 42, "username": "@oncall"},
#   "sent_at": "2026-10-24T10:00:00+00:00"
# }
