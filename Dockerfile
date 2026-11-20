# syntax=docker/dockerfile:1
# Продакшен-образ: slim, без dev-зависимостей, непривилегированный пользователь.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY data/demo_catalog.json ./data/demo_catalog.json
COPY deploy/migrate.sh ./deploy/migrate.sh

RUN useradd --create-home --uid 10001 appuser
USER appuser

# Версия образа видна в логах и метриках (передаётся из CI: --build-arg APP_VERSION=<git sha>)
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-access-log"]
