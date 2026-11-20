#!/bin/sh
# Применить миграции и (опционально) загрузить демо-каталог. Оба шага идемпотентны.
set -e
alembic upgrade head
if [ "${SEED_DEMO:-false}" = "true" ]; then
  python -m app.cli import-catalog data/demo_catalog.json
fi
