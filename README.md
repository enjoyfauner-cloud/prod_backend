# Советник: какую карту выбрать для покупки (backend)

Сервис сравнивает условия кэшбэка банковских карт для конкретной покупки (категория + сумма), считает выгоду в рублях с разбором «как посчитано» и рекомендует топ карт. Работает по всему каталогу и отдельно по «моим картам» с учётом уже использованного месячного лимита.

> **Каталог демонстрационный.** Банки и условия вымышлены и сгенерированы для олимпиады.

Стек: Python 3.13, FastAPI, Pydantic v2, PostgreSQL 17, SQLAlchemy 2 (async), Alembic, Prometheus, Grafana, Docker Compose, GitHub Actions.

## Запуск одной командой

```bash
docker compose up --build -d
```

Поднимутся `postgres`, `migrate` (миграции + импорт демо-каталога), `api`, `prometheus`, `grafana`.

| Что | Адрес |
|---|---|
| Swagger UI | http://localhost:8000/docs |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Health / Ready | http://localhost:8000/health, http://localhost:8000/ready |
| Метрики | http://localhost:8000/metrics |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3001 (admin / admin), дашборд **Sovetnik → «Советник — API»** |
| Postgres | localhost:**5433** (порт хоста меняется через `POSTGRES_HOST_PORT`) |

Проверка сценария из кейса:
```bash
curl -s -X POST http://localhost:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"category":"supermarkets","amount":"2500.00"}' | python3 -m json.tool | head -40
```

## Локальная разработка

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env
docker compose up -d postgres            # только БД
make migrate seed                        # миграции + демо-каталог
.venv/bin/uvicorn app.main:create_app --factory --reload
```

## Тесты и проверки

```bash
docker compose up -d postgres   # интеграционным тестам нужен Postgres (БД sovetnik_test создаётся автоматически)
make lint                       # ruff check + ruff format --check
make typecheck                  # pyright
make test                       # все тесты: юнит + интеграционные
make test-unit                  # только формула и валидация, без БД
```

- `tests/unit` проверяет формулу, округление, лимиты, тай-брейки, истёкшие условия, повторяемость и валидацию справочника.
- `tests/integration` работает с настоящим приложением и Postgres: все критерии MVP, «мои карты», учёт лимита, ошибки, идемпотентный импорт, 503 при недоступной БД. Схема создаётся миграциями (`downgrade base → upgrade head`).

## Данные

```bash
python -m app.cli import-catalog data/demo_catalog.json         # идемпотентный импорт (JSON или CSV)
python -m scripts.generate_catalog --count 42                   # пересобрать демо (50 карт, детерминированно)
python -m scripts.generate_catalog --count 600 --out data/generated_600.json   # большой каталог
python -m scripts.benchmark                                     # нагрузка на 608 картах (тестовая БД!)
```

## Документация

- [docs/api-contract.md](docs/api-contract.md): контракт API для frontend.
- [docs/plan.md](docs/plan.md): архитектура, модель данных, формула, ранжирование.
- [docs/deploy.md](docs/deploy.md): деплой, откат образа и миграций, поведение при сбоях.
- [DEFENSE.md](DEFENSE.md): материалы к защите, ключевые решения и вопросы жюри.
- [NOTES.md](NOTES.md): журнал работы и принятых решений.

## Структура

```
app/            api/ (роутеры), schemas/, services/ (calculator — чистая формула, repository — SQL,
                catalog_import), observability/ (JSON-логи, метрики, middleware), models.py, money.py
alembic/        миграции
data/           демо-каталог
scripts/        генератор каталога, бенчмарк
tests/          unit/, integration/
deploy/         prometheus, grafana (datasource, дашборд, алерты), migrate.sh
```
