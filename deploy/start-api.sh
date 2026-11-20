#!/bin/sh
# Запуск API: несколько процессов uvicorn (WEB_CONCURRENCY) + общий каталог метрик Prometheus.
set -e
if [ -n "$PROMETHEUS_MULTIPROC_DIR" ]; then
  rm -rf "$PROMETHEUS_MULTIPROC_DIR" && mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
fi
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 \
  --proxy-headers --no-access-log --workers "${WEB_CONCURRENCY:-1}"
