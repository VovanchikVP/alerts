# Alerts Bot

Телеграм-бот дежурного: принимает алерты с серверов по HTTP и отправляет обратно на сервер
действия, которые дежурный выбирает кнопками под сообщением.

```
сервер ──POST /api/alerts (X-API-Key)──▶ бот ──▶ Telegram (сообщение + кнопки)
                                           ▲                     │
                                           └── нажатие кнопки ◀───┘
                                                    │
сервер ◀──POST callback_url (Bearer + HMAC)─────────┘
```

## Возможности

- приём алертов по HTTP с авторизацией по `X-API-Key`;
- уровни важности `info / warning / error / critical` и фильтр на чат (`/level`);
- схлопывание повторов по `dedup_key` — повторный алерт **редактирует** уже отправленное
  сообщение, а не присылает новое;
- кнопки действий из самого алерта: перезапуск сервиса, очистка логов, запуск плейбука —
  бот дергает ваш webhook с подписью HMAC-SHA256;
- подтверждение для опасных действий (`"confirm": true`);
- статусы «принят в работу» / «закрыт» и журнал действий прямо в сообщении (кто, что, когда);
- тишина на время (`/mute 2h`), при этом `critical` приходит всегда;
- whitelist чатов, SQLite для состояния — никакой внешней инфраструктуры не нужно.

## Запуск

```bash
uv sync
cp .env.example .env     # вписать токен бота и ключи
uv run python -m alerts_bot
```

Поднимаются сразу две вещи: long polling Telegram и HTTP API на `ALERTS_API_PORT` (8080).

В Telegram: `/start` в нужном чате. Если чат не в `ALERTS_ALLOWED_CHAT_IDS`, бот ответит
его `chat_id` — добавьте в переменную и перезапустите.

## Отправка алерта с сервера

```bash
curl -X POST http://bot-host:8080/api/alerts \
  -H "X-API-Key: $ALERTS_INGEST_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Диск заполнен на 94%",
    "message": "/dev/sda1 — осталось 3.1 GiB из 50 GiB",
    "severity": "error",
    "source": "web-01.prod",
    "dedup_key": "disk-full:web-01",
    "labels": {"env": "prod", "service": "nginx"},
    "actions": [
      {"id": "cleanup_logs", "title": "🧹 Почистить логи", "payload": {"older_than_days": 7}},
      {"id": "restart_nginx", "title": "♻️ Перезапустить", "payload": {"unit": "nginx"}, "confirm": true}
    ],
    "callback_url": "https://web-01.prod:9000/hooks/telegram-action"
  }'
```

Ответ: `{"id": 12, "created": true, "delivered_to": [-1001234567890], ...}`

Закрыть алерт со стороны сервера — повторить запрос с `"status": "resolved"` и тем же
`dedup_key`, либо `POST /api/alerts/{id}/resolve`. Сообщение в чате обновится само.

### Поля алерта

| поле | обязательное | описание |
|---|---|---|
| `title` | да | заголовок |
| `message` | нет | подробности |
| `severity` | нет | `info` / `warning` / `error` / `critical` (по умолчанию `warning`) |
| `source` | нет | хост или сервис |
| `dedup_key` | нет | ключ схлопывания повторов |
| `labels` | нет | словарь меток, выводится в сообщении |
| `actions` | нет | до 8 кнопок (`id`, `title`, `payload`, `confirm`, `url`, `method`) |
| `callback_url` | нет | куда слать действия; иначе `ALERTS_DEFAULT_CALLBACK_URL` |
| `status` | нет | `firing` / `acknowledged` / `resolved` |

## Приём действий на сервере

Нажатие кнопки → бот шлёт `POST` на `callback_url`:

```json
{
  "alert_id": 12,
  "dedup_key": "disk-full:web-01",
  "source": "web-01.prod",
  "labels": {"env": "prod"},
  "action": {"id": "restart_nginx", "title": "♻️ Перезапустить", "payload": {"unit": "nginx"}},
  "actor": {"chat_id": -1001234567890, "user_id": 42, "username": "@oncall"},
  "sent_at": "2026-10-24T10:00:00+00:00"
}
```

Заголовки: `Authorization: Bearer <ALERTS_OUTBOUND_TOKEN>` и
`X-Signature: <hmac_sha256(ALERTS_OUTBOUND_SECRET, тело)>`.

Ответ `2xx` — дежурный увидит ✅ и тело ответа в журнале под алертом; любой другой код или
таймаут — ❌ и текст ошибки. Готовый пример приёмника с проверкой подписи:
`examples/action_receiver.py`.

## Команды бота

| команда | что делает |
|---|---|
| `/start` | подписать чат на алерты |
| `/stop` | отписаться |
| `/alerts` | список открытых алертов |
| `/level error` | минимальная важность для этого чата |
| `/mute 2h` | тишина (`critical` всё равно придёт) |
| `/unmute` | снять тишину |
| `/id` | показать `chat_id` |

## Безопасность

- `ALERTS_INGEST_API_KEY` сравнивается через `hmac.compare_digest`; API держите за VPN или
  reverse-proxy с TLS;
- `ALERTS_ALLOWED_CHAT_IDS` — белый список чатов, проверяется middleware на каждом апдейте,
  а не только на `/start`;
- исходящие действия подписываются HMAC — сервер обязан проверять подпись перед выполнением
  команды, иначе кто угодно сможет дернуть ваш webhook;
- опасные действия помечайте `"confirm": true`;
- всё, что приходит из Telegram, экранируется перед вставкой в HTML-сообщение.

## Структура

```
alerts_bot/
  app.py        # сборка и запуск: polling + uvicorn в одном процессе
  api.py        # FastAPI: приём алертов с серверов
  bot.py        # команды, кнопки, контроль доступа
  notifier.py   # доставка в чаты и обновление сообщений
  actions.py    # исходящие действия на сервер + подпись
  render.py     # текст сообщения и клавиатуры
  storage.py    # SQLite: подписчики, алерты, доставки, журнал
  models.py     # схемы API и severity
deploy/k8s/     # манифесты для k3s (namespace alerts)
deploy/ci/      # RBAC для CI и выпуск узкого kubeconfig
.github/        # workflows CI и CD
examples/       # отправка алерта и приёмник действий
tests/          # 34 теста, включая прогон хендлеров с подменённой сессией Telegram
Dockerfile      # multi-stage, non-root, работает с readOnlyRootFilesystem
```

## Разработка

```bash
uv sync --all-groups        # зависимости вместе с dev-группой
uv run pre-commit install   # хуки на коммит
uv run ruff check . && uv run ruff format .
uv run pre-commit run --all-files
uv run pytest               # тесты
```

Набор хуков тот же, что в `rtt-monitoring`: базовые проверки pre-commit-hooks
(trailing-whitespace, end-of-file-fixer, check-yaml с `--allow-multiple-documents`
под манифесты k8s, check-toml, large-files, merge-conflict, debug-statements,
detect-private-key), `ruff-check --fix`, `ruff-format` и `uv-lock`.

## Деплой в k3s через GitHub Actions

Два workflow, по образцу `rtt-monitoring`:

- `.github/workflows/ci.yml` — ruff, pytest и сборка kustomize-оверлея на каждый PR;
- `.github/workflows/cd.yml` — образ в GHCR → WireGuard → `kustomize build | kubectl apply`.

Кластер не виден из интернета, поэтому раннер сам поднимается пиром в сеть WireGuard и
только оттуда ходит в API кластера.

### 1. Секреты и переменные репозитория

**Secrets:**

| секрет | зачем |
|---|---|
| `WG_CONFIG` | конфиг WireGuard-пира для раннера, в base64 |
| `KUBE_API` | адрес API кластера в сети WG — у вас `10.8.0.2:6443` |
| `KUBE_CONFIG` | kubeconfig в base64 (см. п. 3 — узкий доступ) |
| `ALERTS_BOT_TOKEN` | токен бота от @BotFather |
| `ALERTS_INGEST_API_KEY` | ключ для заголовка `X-API-Key` |
| `ALERTS_OUTBOUND_TOKEN` | Bearer для вашего webhook |
| `ALERTS_OUTBOUND_SECRET` | секрет HMAC-подписи действий |
| `GHCR_PULL_TOKEN` | *опционально*: PAT с `read:packages`, если пакет приватный |

**Variables:**

| переменная | пример |
|---|---|
| `ALERTS_ALLOWED_CHAT_IDS` | `-1001234567890,987654` |
| `ALERTS_DEFAULT_CALLBACK_URL` | `https://ops.example.com/hooks/telegram-action` |
| `ALERTS_INGRESS_HOST` | `alerts.example.com` |

Плюс Environment `production` — можно повесить ручное подтверждение выкатки.

Секрет `alerts-secrets` в кластере пересоздаётся на каждом деплое из этих значений, руками
в кластер ничего класть не нужно.

> Секреты Actions живут на уровне репозитория, а у личных аккаунтов общих секретов нет:
> значения из `rtt-monitoring` здесь не видны и выгрузить их из GitHub нельзя — секреты
> write-only. Поэтому `WG_CONFIG`, `KUBE_API` и `KUBE_CONFIG` заводятся для этого
> репозитория заново, по пунктам 2 и 3.

### 2. WireGuard: отдельный пир для этого раннера

Переиспользовать пир `rtt-monitoring` (`10.8.0.50`) не стоит — один ключ на двух раннерах
ломает хендшейки, если выкатки пересекутся по времени. На сервере WireGuard:

```bash
wg genkey | tee runner-alerts.key | wg pubkey > runner-alerts.pub

# на сервере в [Peer] идёт ПУБЛИЧНЫЙ ключ раннера
sudo wg set wg0 peer "$(cat runner-alerts.pub)" allowed-ips 10.8.0.51/32
sudo tee -a /etc/wireguard/wg0.conf > /dev/null <<EOF

[Peer]
# github actions runner (alerts)
PublicKey = $(cat runner-alerts.pub)
AllowedIPs = 10.8.0.51/32
EOF

sudo wg show wg0
```

Теперь конфиг самого раннера — он уходит в секрет `WG_CONFIG`. Ключи асимметричны: у
раннера свой приватный, а в `[Peer]` — публичный ключ **сервера**, не раннера.

```bash
sudo wg show wg0 public-key     # → PublicKey в [Peer] ниже
sudo wg show wg0 listen-port    # → порт для Endpoint
curl -4 -s ifconfig.me          # → внешний адрес сервера, если нет DNS-имени
```

```ini
[Interface]
PrivateKey = <содержимое runner-alerts.key>
Address = 10.8.0.51/32

[Peer]
# публичный ключ СЕРВЕРА (wg show wg0 public-key), не runner-alerts.pub
PublicKey = <публичный ключ сервера>
# адрес, по которому раннер GitHub достучится до сервера ИЗ ИНТЕРНЕТА:
# публичный IP или DNS-имя машины с wg0, не 10.8.0.x — через туннель
# до самого туннеля не дойти
Endpoint = <внешний адрес сервера>:51820
# что ходит через туннель: сеть WG и подсеть подов кластера
AllowedIPs = 10.8.0.0/24, 10.42.0.0/16
PersistentKeepalive = 25
```

Оба значения — `PublicKey` и `Endpoint` — те же, что в конфиге пира `rtt-monitoring`;
отличаются только `PrivateKey` и `Address`.

Проверить до того, как класть в секрет (с машины снаружи сети):

```bash
sudo wg-quick up ./wg-alerts.conf
nc -z -w5 10.8.0.2 6443 && echo "API кластера виден"
sudo wg-quick down ./wg-alerts.conf

base64 -i wg-alerts.conf | pbcopy   # → секрет WG_CONFIG
```

`KUBE_API` при этом = `10.8.0.2:6443` — тот же адрес, что у `rtt-monitoring`.

### 3. Узкий доступ в кластер

Вместо админского kubeconfig этот проект получает ServiceAccount, который не видит ничего
за пределами namespace `alerts`. На ноде k3s:

```bash
./deploy/ci/make-kubeconfig.sh https://10.8.0.2:6443 > kubeconfig.b64
```

Адрес должен быть тем, который виден в сети WG — он уже есть в `tls-san` ноды, раз
`rtt-monitoring` по нему ходит, так что сертификат перевыпускать не нужно.

Скрипт применяет `deploy/ci/deployer-rbac.yaml` (ServiceAccount `alerts-deployer`, Role в
namespace `alerts` и минимальный ClusterRole только на `namespaces` — namespace создаётся
манифестами, а это ресурс кластерного уровня) и печатает готовый base64-kubeconfig.

#### Первый выпуск, когда проекта на ноде ещё нет

Те же действия без репозитория — блоки вставляются в терминал ноды целиком.

**1. Namespace и RBAC:**

```bash
sudo k3s kubectl create namespace alerts --dry-run=client -o yaml | sudo k3s kubectl apply -f -

sudo k3s kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: alerts-deployer
  namespace: alerts
---
apiVersion: v1
kind: Secret
metadata:
  name: alerts-deployer-token
  namespace: alerts
  annotations:
    kubernetes.io/service-account.name: alerts-deployer
type: kubernetes.io/service-account-token
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: alerts-deployer
  namespace: alerts
rules:
  - apiGroups: [""]
    resources: [secrets, services, persistentvolumeclaims, configmaps]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [""]
    resources: [pods, pods/log, events]
    verbs: [get, list, watch]
  - apiGroups: [apps]
    resources: [deployments, replicasets]
    verbs: [get, list, watch, create, update, patch, delete]
  - apiGroups: [networking.k8s.io]
    resources: [ingresses]
    verbs: [get, list, watch, create, update, patch, delete]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: alerts-deployer
  namespace: alerts
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: alerts-deployer
subjects:
  - kind: ServiceAccount
    name: alerts-deployer
    namespace: alerts
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: alerts-namespace-bootstrap
rules:
  - apiGroups: [""]
    resources: [namespaces]
    verbs: [get, create, patch]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: alerts-namespace-bootstrap
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: alerts-namespace-bootstrap
subjects:
  - kind: ServiceAccount
    name: alerts-deployer
    namespace: alerts
EOF
```

**2. Собрать kubeconfig** (токен выпускается контроллером за секунду-другую):

```bash
SERVER=https://10.8.0.2:6443

TOKEN=$(sudo k3s kubectl -n alerts get secret alerts-deployer-token -o jsonpath='{.data.token}' | base64 -d)
CA=$(sudo k3s kubectl -n alerts get secret alerts-deployer-token -o jsonpath='{.data.ca\.crt}')
[ -n "$TOKEN" ] || echo "токен ещё не выпущен, повторите команду через пару секунд"

cat > /tmp/alerts-kubeconfig.yaml <<EOF
apiVersion: v1
kind: Config
current-context: alerts
clusters:
  - name: k3s
    cluster:
      server: ${SERVER}
      certificate-authority-data: ${CA}
contexts:
  - name: alerts
    context:
      cluster: k3s
      namespace: alerts
      user: alerts-deployer
users:
  - name: alerts-deployer
    user:
      token: ${TOKEN}
EOF
```

**3. Проверить права прямо на ноде:**

```bash
KC=/tmp/alerts-kubeconfig.yaml
sudo k3s kubectl --kubeconfig $KC -n alerts get pods          # работает (список пуст)
sudo k3s kubectl --kubeconfig $KC auth can-i --list -n alerts # что разрешено
sudo k3s kubectl --kubeconfig $KC get nodes                   # должно быть Forbidden
```

**4. Выдать значение для секрета и убрать временный файл:**

```bash
base64 -w0 /tmp/alerts-kubeconfig.yaml; echo
shred -u /tmp/alerts-kubeconfig.yaml 2>/dev/null || rm -f /tmp/alerts-kubeconfig.yaml
```

Строку целиком — в GitHub: *Settings → Secrets and variables → Actions → New repository
secret*, имя `KUBE_CONFIG`.

**5. Проверить с ноутбука через туннель** (когда WireGuard заработает):

```bash
echo '<вставленная строка base64>' | base64 -d > /tmp/kc.yaml
KUBECONFIG=/tmp/kc.yaml kubectl -n alerts get pods
rm -f /tmp/kc.yaml
```

Дальше, когда репозиторий окажется на ноде, то же самое делает
`./deploy/ci/make-kubeconfig.sh https://10.8.0.2:6443`.

Проверка прав в одну строку:

```bash
kubectl auth can-i --list --as=system:serviceaccount:alerts:alerts-deployer -n alerts
kubectl auth can-i delete nodes --as=system:serviceaccount:alerts:alerts-deployer  # no
```

### 4. Что именно разворачивается

- `Deployment` в одной реплике со стратегией `Recreate` — два пода читали бы один и тот же
  long polling (Telegram отдаст 409) и писали бы в одну базу SQLite;
- `PersistentVolumeClaim` 1Gi на `local-path` (дефолтный storage class k3s) под `/data`;
- контейнер — non-root (uid 10001), `readOnlyRootFilesystem`, все capabilities сброшены,
  запись только в `/data` и `/tmp`;
- `Service` ClusterIP :8080 и `Ingress` на Traefik для `/api` — именно туда ваши серверы
  шлют алерты;
- пробы на `/healthz`: приём алертов поднимается независимо от Telegram, поэтому обрыв
  связи с `api.telegram.org` не делает под not-ready — polling переподключается сам.

Образ — `ghcr.io/<owner>/alerts:<sha>`, тег по коммиту, поэтому откат это
`kubectl -n alerts rollout undo deployment/alerts-bot`. Для k3s на ARM добавьте
`linux/arm64` в `platforms` шага сборки.

### 5. Ручная выкатка

```bash
cd deploy/k8s
kustomize edit set image alerts-bot=ghcr.io/<owner>/alerts:<tag>
kubectl apply -k .
kubectl -n alerts rollout status deployment/alerts-bot
kubectl -n alerts logs deployment/alerts-bot -f
```

Обычно этого делать не нужно — то же самое выполняет workflow `CD`.

Проверка приёма снаружи:

```bash
curl -fsS https://alerts.example.com/healthz
```
