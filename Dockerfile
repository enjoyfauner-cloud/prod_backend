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
COPY deploy/migrate.sh deploy/start-api.sh ./deploy/

RUN useradd --create-home --uid 10001 appuser
USER appuser

# Версия образа видна в логах и метриках (передаётся из CI: --build-arg APP_VERSION=<git sha>)
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# Процессы uvicorn (по числу ядер, которые готовы отдать API) и общий каталог метрик
ENV WEB_CONCURRENCY=4 \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus

EXPOSE 8000
CMD ["sh", "/app/deploy/start-api.sh"]
