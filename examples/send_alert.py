"""Пример: сервер отправляет алерт боту.

python examples/send_alert.py
"""

import httpx

API = "http://127.0.0.1:8080/api/alerts"
API_KEY = "смените-меня"

alert = {
    "title": "Диск заполнен на 94%",
    "message": "/dev/sda1 — осталось 3.1 GiB из 50 GiB",
    "severity": "error",
    "source": "web-01.prod",
    "dedup_key": "disk-full:web-01:/dev/sda1",
    "labels": {"env": "prod", "service": "nginx", "mount": "/"},
    "actions": [
        {"id": "cleanup_logs", "title": "🧹 Почистить логи", "payload": {"older_than_days": 7}},
        {
            "id": "restart_nginx",
            "title": "♻️ Перезапустить nginx",
            "payload": {"unit": "nginx.service"},
            "confirm": True,
        },
        {"id": "run_playbook", "title": "📦 Ansible: disk-cleanup", "confirm": True},
    ],
    # Куда бот отправит нажатое действие; если не указать — ALERTS_DEFAULT_CALLBACK_URL.
    "callback_url": "http://127.0.0.1:9000/hooks/telegram-action",
}

response = httpx.post(API, json=alert, headers={"X-API-Key": API_KEY}, timeout=15)
response.raise_for_status()
print(response.json())
