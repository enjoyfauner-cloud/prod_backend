# Деплой и откат

## Как устроен релиз

```
git push main → GitHub Actions: ruff → pyright → pytest (+ Postgres) → миграции вверх/вниз → docker build
             → образ ghcr.io/<owner>/<repo>:<git-sha> (+ :latest)
сервер:      API_IMAGE=…:<sha> docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
             → postgres (healthy) → migrate (alembic upgrade head + импорт демо, идемпотентно)
             → api (стартует только после успешного migrate; healthcheck = /ready)
```

- Каждый образ помечен **git sha**, поэтому всегда видно, что именно запущено (`APP_VERSION` в логе старта).
- Миграции выполняет **отдельный одноразовый контейнер** `migrate`, а не каждая реплика API. Если миграция упала, API не стартует, и старая версия продолжает работать.
- Конфигурация задаётся только через переменные окружения (`.env` на сервере, пример в `.env.example`).

## Первый деплой на сервер с Docker

```bash
git clone <repo> sovetnik && cd sovetnik
cp .env.example .env     # задать POSTGRES_PASSWORD, GRAFANA_ADMIN_PASSWORD, CORS_ORIGINS
export API_IMAGE=ghcr.io/<owner>/<repo>:<git-sha>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
curl -fsS http://localhost:8000/ready
```

## Проверка после релиза (smoke)

```bash
curl -fsS http://<host>:8000/health
curl -fsS http://<host>:8000/ready
curl -fsS -X POST http://<host>:8000/api/v1/recommendations \
  -H 'Content-Type: application/json' -d '{"category":"supermarkets","amount":"2500"}'
```
В Grafana на дашборде «Советник — API» доля 5xx должна быть около 0, а p95 меньше 500 мс.

## Откат

### 1. Откат образа (код)
```bash
export API_IMAGE=ghcr.io/<owner>/<repo>:<предыдущий-sha>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d api
```
Схема БД при этом не меняется. Это безопасно, если новая миграция была обратно совместимой (см. правило ниже).

### 2. Откат миграции (схема)
```bash
# посмотреть текущую ревизию и историю
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic history
# откатить на одну ревизию назад (или на конкретную: alembic downgrade 0001)
docker compose run --rm migrate alembic downgrade -1
```
Порядок: **сначала откатить образ, потом схему**. Старый код не должен работать с новой схемой дольше, чем нужно.

### Правило для миграций (как снижаем риск)
- Каждая миграция имеет рабочий `downgrade()`. CI проверяет цикл `upgrade head → downgrade base → upgrade head` и `alembic check` (модели = схема).
- Ломающие изменения разбиваем на два релиза (expand/contract). Сначала добавляем новую колонку или таблицу, код пишет в обе. Старое удаляем в следующем релизе. Тогда откат образа никогда не требует отката схемы.
- Перед миграцией в проде делаем резервную копию: `docker compose exec postgres pg_dump -U sovetnik sovetnik > backup_$(date +%F).sql`.

## Сбои и как система их переживает
| Сбой | Поведение |
|---|---|
| БД недоступна | `/health` = 200 (процесс жив), `/ready` = 503. API отвечает `503 service_unavailable`, а не 500. `pool_pre_ping` восстанавливает соединения после рестарта Postgres |
| Миграция упала | контейнер `migrate` завершается с ошибкой, новый `api` не стартует |
| Битый файл справочника | импорт проверяет весь файл заранее и работает в одной транзакции: либо применяется весь, либо ничего |
| Необработанное исключение | клиент получает `500 internal_error` с `request_id`, стек пишется в JSON-лог, растёт `app_errors_total{code="internal_error"}`, срабатывает алерт доли 5xx |
| Падение процесса API | `restart: unless-stopped`, healthcheck по `/ready` |
