# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.8-python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Зависимости отдельным слоем: пересобираются только при изменении lock-файла.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY alerts_bot ./alerts_bot
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ALERTS_DB_PATH=/data/alerts.db

USER app
EXPOSE 8080

# uv в рантайме не нужен — запускаем интерпретатор из собранного venv,
# поэтому контейнер работает с readOnlyRootFilesystem.
CMD ["python", "-m", "alerts_bot"]
