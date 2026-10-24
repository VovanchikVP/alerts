from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from .config import Settings
from .models import AlertIn, AlertOut, AlertStatus
from .notifier import Notifier
from .storage import Storage

log = logging.getLogger(__name__)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_storage(request: Request) -> Storage:
    return request.app.state.storage


def get_notifier(request: Request) -> Notifier:
    return request.app.state.notifier


# Зависимости через Annotated — рекомендованный стиль FastAPI; вызов Depends()
# в значении по умолчанию ловит линтер (B008).
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[Storage, Depends(get_storage)]
NotifierDep = Annotated[Notifier, Depends(get_notifier)]
ApiKeyDep = Annotated[str | None, Header(alias="X-API-Key")]


async def require_api_key(settings: SettingsDep, x_api_key: ApiKeyDep = None) -> None:
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.ingest_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "неверный или отсутствующий X-API-Key")


def create_app(settings: Settings, storage: Storage, notifier: Notifier) -> FastAPI:
    app = FastAPI(
        title="Alerts intake API",
        description="Приём алертов с серверов и доставка их дежурным в Telegram",
        version="1.0.0",
    )
    app.state.settings = settings
    app.state.storage = storage
    app.state.notifier = notifier

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/alerts", response_model=AlertOut, dependencies=[Depends(require_api_key)])
    async def ingest(payload: AlertIn, storage: StorageDep, notifier: NotifierDep) -> AlertOut:
        alert, created = await storage.upsert_alert(payload)
        delivered, failed = await notifier.dispatch(alert)
        log.info(
            "алерт %s (%s) %s, доставлен в %d чат(ов)",
            alert.id,
            alert.severity.value,
            "создан" if created else "обновлён",
            len(delivered),
        )
        return AlertOut(
            id=alert.id,
            dedup_key=alert.dedup_key,
            status=alert.status,
            created=created,
            delivered_to=delivered,
            failed_for=failed,
        )

    @app.post(
        "/api/alerts/{alert_id}/resolve",
        response_model=AlertOut,
        dependencies=[Depends(require_api_key)],
    )
    async def resolve(alert_id: int, storage: StorageDep, notifier: NotifierDep) -> AlertOut:
        alert = await storage.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "алерт не найден")
        await storage.set_status(alert_id, AlertStatus.RESOLVED, "server")
        updated = await storage.get_alert(alert_id)
        assert updated is not None
        await notifier.refresh(updated)
        return AlertOut(
            id=updated.id,
            dedup_key=updated.dedup_key,
            status=updated.status,
            created=False,
            delivered_to=[chat_id for chat_id, _ in await storage.list_deliveries(alert_id)],
        )

    return app
