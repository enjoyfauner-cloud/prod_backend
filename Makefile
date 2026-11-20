.PHONY: up down logs install lint typecheck test test-unit migrate seed catalog-large

up:            ## Поднять весь стек (api, postgres, prometheus, grafana)
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

install:       ## Dev-зависимости в .venv
	.venv/bin/pip install -r requirements-dev.txt

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

typecheck:
	.venv/bin/pyright

test-unit:
	.venv/bin/pytest tests/unit -q

test:          ## Все тесты (нужен Postgres: docker compose up -d postgres)
	.venv/bin/pytest -q

migrate:
	.venv/bin/alembic upgrade head

seed:
	.venv/bin/python -m app.cli import-catalog data/demo_catalog.json

catalog-large: ## Каталог на 600+ карт для проверки производительности
	.venv/bin/python -m scripts.generate_catalog --count 600 --out data/generated_600.json
