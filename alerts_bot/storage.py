from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

import aiosqlite

from .models import Action, AlertIn, AlertStatus, Severity

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id       INTEGER PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    min_severity  TEXT NOT NULL DEFAULT 'info',
    muted_until   TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key    TEXT,
    title        TEXT NOT NULL,
    message      TEXT NOT NULL DEFAULT '',
    severity     TEXT NOT NULL,
    source       TEXT,
    labels       TEXT NOT NULL DEFAULT '{}',
    actions      TEXT NOT NULL DEFAULT '[]',
    callback_url TEXT,
    status       TEXT NOT NULL,
    actor        TEXT,
    started_at   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS alerts_dedup_open
    ON alerts (dedup_key) WHERE dedup_key IS NOT NULL AND status != 'resolved';

CREATE TABLE IF NOT EXISTS deliveries (
    alert_id   INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    PRIMARY KEY (alert_id, chat_id)
);

CREATE TABLE IF NOT EXISTS action_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id   INTEGER NOT NULL,
    action_id  TEXT NOT NULL,
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER,
    username   TEXT,
    ok         INTEGER NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class Alert:
    id: int
    dedup_key: str | None
    title: str
    message: str
    severity: Severity
    source: str | None
    labels: dict[str, str]
    actions: list[Action]
    callback_url: str | None
    status: AlertStatus
    actor: str | None
    started_at: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Self:
        return cls(
            id=row["id"],
            dedup_key=row["dedup_key"],
            title=row["title"],
            message=row["message"],
            severity=Severity(row["severity"]),
            source=row["source"],
            labels=json.loads(row["labels"]),
            actions=[Action.model_validate(item) for item in json.loads(row["actions"])],
            callback_url=row["callback_url"],
            status=AlertStatus(row["status"]),
            actor=row["actor"],
            started_at=row["started_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def action_by_index(self, index: int) -> Action | None:
        if 0 <= index < len(self.actions):
            return self.actions[index]
        return None


@dataclass(slots=True)
class Subscriber:
    chat_id: int
    title: str
    min_severity: Severity
    muted_until: datetime | None

    def wants(self, severity: Severity, now: datetime | None = None) -> bool:
        if severity.rank < self.min_severity.rank:
            return False
        # Critical пробивает mute — иначе от «тишины» теряется смысл дежурства.
        if severity is Severity.CRITICAL:
            return True
        if self.muted_until is None:
            return True
        return (now or datetime.now(UTC)) >= self.muted_until


class Storage:
    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage не подключён: вызовите connect()")
        return self._db

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ---------- подписчики ----------

    async def add_subscriber(self, chat_id: int, title: str) -> None:
        await self.db.execute(
            """
            INSERT INTO subscribers (chat_id, title, created_at) VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title
            """,
            (chat_id, title, _now()),
        )
        await self.db.commit()

    async def remove_subscriber(self, chat_id: int) -> bool:
        cursor = await self.db.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        await self.db.commit()
        return cursor.rowcount > 0

    async def get_subscriber(self, chat_id: int) -> Subscriber | None:
        async with self.db.execute(
            "SELECT * FROM subscribers WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return _subscriber(row) if row else None

    async def list_subscribers(self) -> list[Subscriber]:
        async with self.db.execute("SELECT * FROM subscribers ORDER BY chat_id") as cursor:
            return [_subscriber(row) for row in await cursor.fetchall()]

    async def set_mute(self, chat_id: int, until: datetime | None) -> None:
        await self.db.execute(
            "UPDATE subscribers SET muted_until = ? WHERE chat_id = ?",
            (until.isoformat() if until else None, chat_id),
        )
        await self.db.commit()

    async def set_min_severity(self, chat_id: int, severity: Severity) -> None:
        await self.db.execute(
            "UPDATE subscribers SET min_severity = ? WHERE chat_id = ?",
            (severity.value, chat_id),
        )
        await self.db.commit()

    # ---------- алерты ----------

    async def upsert_alert(self, payload: AlertIn) -> tuple[Alert, bool]:
        """Создаёт алерт или обновляет открытый алерт с тем же dedup_key."""
        now = _now()
        existing: aiosqlite.Row | None = None
        if payload.dedup_key:
            async with self.db.execute(
                """
                SELECT * FROM alerts
                WHERE dedup_key = ? AND status != 'resolved'
                ORDER BY id DESC LIMIT 1
                """,
                (payload.dedup_key,),
            ) as cursor:
                existing = await cursor.fetchone()

        values = (
            payload.title,
            payload.message,
            payload.severity.value,
            payload.source,
            json.dumps(payload.labels, ensure_ascii=False),
            json.dumps(
                [action.model_dump(mode="json", exclude_none=True) for action in payload.actions],
                ensure_ascii=False,
            ),
            str(payload.callback_url) if payload.callback_url else None,
            payload.status.value,
            payload.started_at.isoformat(),
        )

        if existing is not None:
            await self.db.execute(
                """
                UPDATE alerts SET title = ?, message = ?, severity = ?, source = ?,
                    labels = ?, actions = ?, callback_url = ?, status = ?, started_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (*values, now, existing["id"]),
            )
            await self.db.commit()
            alert = await self.get_alert(existing["id"])
            assert alert is not None
            return alert, False

        cursor = await self.db.execute(
            """
            INSERT INTO alerts (dedup_key, title, message, severity, source, labels, actions,
                                callback_url, status, started_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (payload.dedup_key, *values, now, now),
        )
        await self.db.commit()
        alert = await self.get_alert(cursor.lastrowid)
        assert alert is not None
        return alert, True

    async def get_alert(self, alert_id: int) -> Alert | None:
        async with self.db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)) as cursor:
            row = await cursor.fetchone()
        return Alert.from_row(row) if row else None

    async def set_status(self, alert_id: int, status: AlertStatus, actor: str | None) -> None:
        await self.db.execute(
            "UPDATE alerts SET status = ?, actor = ?, updated_at = ? WHERE id = ?",
            (status.value, actor, _now(), alert_id),
        )
        await self.db.commit()

    async def list_open_alerts(self, limit: int = 20) -> list[Alert]:
        async with self.db.execute(
            "SELECT * FROM alerts WHERE status != 'resolved' ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            return [Alert.from_row(row) for row in await cursor.fetchall()]

    # ---------- доставка и журнал ----------

    async def save_delivery(self, alert_id: int, chat_id: int, message_id: int) -> None:
        await self.db.execute(
            """
            INSERT INTO deliveries (alert_id, chat_id, message_id) VALUES (?, ?, ?)
            ON CONFLICT(alert_id, chat_id) DO UPDATE SET message_id = excluded.message_id
            """,
            (alert_id, chat_id, message_id),
        )
        await self.db.commit()

    async def list_deliveries(self, alert_id: int) -> list[tuple[int, int]]:
        async with self.db.execute(
            "SELECT chat_id, message_id FROM deliveries WHERE alert_id = ?", (alert_id,)
        ) as cursor:
            return [(row["chat_id"], row["message_id"]) for row in await cursor.fetchall()]

    async def log_action(
        self,
        *,
        alert_id: int,
        action_id: str,
        chat_id: int,
        user_id: int | None,
        username: str | None,
        ok: bool,
        detail: str | None,
    ) -> None:
        await self.db.execute(
            """
            INSERT INTO action_log (alert_id, action_id, chat_id, user_id, username, ok, detail,
                                    created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (alert_id, action_id, chat_id, user_id, username, int(ok), detail, _now()),
        )
        await self.db.commit()

    async def recent_actions(self, alert_id: int, limit: int = 10) -> list[dict[str, Any]]:
        async with self.db.execute(
            "SELECT * FROM action_log WHERE alert_id = ? ORDER BY id DESC LIMIT ?",
            (alert_id, limit),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


def _subscriber(row: aiosqlite.Row) -> Subscriber:
    muted_until = row["muted_until"]
    return Subscriber(
        chat_id=row["chat_id"],
        title=row["title"],
        min_severity=Severity(row["min_severity"]),
        muted_until=datetime.fromisoformat(muted_until) if muted_until else None,
    )
