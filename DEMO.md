# Сценарий демо (5–7 минут)

Цель: за несколько минут показать жюри все критерии MVP и сильные стороны backend.
Порядок построен как история: «пользователь пришёл → получил совет → понял, почему → система надёжна».

## Подготовка (за 10 минут до защиты)

```bash
cd ~/PycharmProjects/python/prod_backend
C="docker compose -p sovetnik-prod --env-file .env.prod -f docker-compose.yml -f docker-compose.prod.yml"
$C up -d && $C ps            # всё Up, api (healthy)
curl -s localhost:8000/ready # {"status":"ready"}
```
Открыть заранее вкладки:
1. Swagger: http://localhost:8000/docs
2. Grafana, дашборд «Советник — API»: http://localhost:3001/d/sovetnik-api (вход admin, пароль в `.env.prod`)
3. GitHub Actions: https://github.com/enjoyfauner-cloud/prod_backend/actions
4. В IDE: `app/services/calculator.py` (функции `calculate` и `ranking_key`)
5. Telegram с ботом алертов

Нагнать трафик, чтобы графики не были пустыми (минуту до показа):
```bash
for i in $(seq 1 200); do for c in supermarkets fuel taxi restaurants; do
  curl -s -o /dev/null -X POST localhost:8000/api/v1/recommendations \
    -H 'Content-Type: application/json' -d "{\"category\":\"$c\",\"amount\":\"2500\"}"; done; done
```

---

## 1. Главный сценарий кейса (1,5 мин), Swagger

`POST /api/v1/recommendations` → Try it out:
```json
{"category": "supermarkets", "amount": "2500.00"}
```
**Говорим:**
- «Топ-3 в рублях: Продуктовая 150 ₽, Двойка 125 ₽, Семейная 125 ₽».
- Раскрыть `top[0].breakdown`: «7% от 2 500 = 175, но лимит 150, поэтому 150. Видно, какая ставка применена и сработал ли лимит. `explanation` можно показать пользователю как есть».
- `terms`: «Каждая карта показана с датами действия и датой проверки условий».
- «У Двойки и Семейной ничья по 125. Двойка выше, потому что у неё нет лимита. Правила тай-брейка детерминированы».
- Пролистать до `outdated`: «Архивная даёт 10%, но условия истекли 30.09. Она помечена и в топ не попадает».

Поменять `category` на `"fuel"` → Execute: **«Другая категория — другие рекомендации»**.
Execute ещё раз с теми же данными: **«Тот же запрос — тот же результат, байт в байт»**.

## 2. Каталог и детали карты (30 сек)

`GET /api/v1/cards?category=pharmacy` → «фильтры, пагинация».
`GET /api/v1/cards/bank-yug-semya` → «подробные условия: ставки и лимиты по категориям, версия 2 (v1 в истории), `is_demo_data: true`».

## 3. Нетривиальная фича: «Мои карты» и остаток лимита (1,5 мин)

В каждом запросе ниже в Swagger есть поле `X-User-Id`, заполняем его:
`3f1c2a9e-8b7d-4c3e-9f10-2a4b6c8d0e1f`

1. `POST /api/v1/me/cards` `{"card_id": "bank-sever-produkty"}`, затем `{"card_id": "bank-yug-semya"}`.
2. `POST /api/v1/recommendations` `{"category":"supermarkets","amount":"2500","scope":"my"}` → первая Продуктовая, 150 ₽.
3. `PUT /api/v1/me/cards/bank-sever-produkty/usage` `{"category":"supermarkets","used_cashback":"140"}`.
4. Повторить шаг 2 → **первой стала Семейная**, у Продуктовой `cashback: "10.00"`, `limit_used_before: "140.00"`.

**Говорим:** «В этом месяце я уже выбрал 140 из 150 ₽ лимита, советник это учёл. Лимит привязан к месяцу и в следующем месяце сбросится сам. Реквизиты карт не храним: только анонимный UUID и ссылка на карту каталога. Лишние поля вроде `cvv` отклоняются с 422».

## 4. Как посчитано: код (45 сек), IDE

`app/services/calculator.py`:
- `calculate`: «формула `min(сумма × ставка, остаток лимита)`, округление вниз до копейки в одном месте, `app/money.py`. Только Decimal».
- `ranking_key`: «порядок: кэшбэк → остаток лимита → срок действия → код карты. `promo_url` в ключе нет, реклама не влияет».
- «Это чистая функция: без БД и без часов. Поэтому повторяемость гарантирована, и на неё 26 юнит-тестов (всего юнит-тестов 57)».

## 5. Наблюдаемость и сбой (1,5 мин), Grafana

Дашборд: «RPS, p95 латентности, доля 5xx, время расчёта, размер каталога».

Показываем сбой в прямом эфире:
```bash
$C stop postgres
curl -s localhost:8000/health   # 200: процесс жив
curl -s localhost:8000/ready    # 503: БД недоступна
curl -s -X POST localhost:8000/api/v1/recommendations -H 'Content-Type: application/json' \
  -d '{"category":"supermarkets","amount":"2500"}'   # 503 service_unavailable + request_id
```
- Скопировать `request_id` из ответа → вставить в поле **request_id** вверху дашборда → панель логов показывает этот запрос. «По жалобе пользователя находим запрос за секунды».
- Нагнать ошибок: `for i in $(seq 1 50); do curl -s -o /dev/null localhost:8000/api/v1/categories; done`.
- Панель «Доля 5xx» краснеет. Через ~2–3 минуты алерт «Доля 5xx > 5%» → **Firing** → сообщение в Telegram.
  Пока ждём алерт, переходим к пункту 6.
```bash
$C start postgres   # после показа алерта; API восстанавливается сам (pool_pre_ping)
```

## 6. Инженерная часть (1 мин)

- **GitHub Actions:** «каждый push проходит lint → типы → 102 теста на настоящем Postgres → миграции вверх/вниз → сборка образа».
- **Миграции и откат:** «образы помечены git sha. Откат кода — это запуск предыдущего тега. Откат схемы — `alembic downgrade`. Мы это репетировали и нашли подвох: откатывать схему надо новым образом» (`docs/deploy.md`).
- **Производительность:** «608 карт: расчёт ~4 мс, полный запрос p95 ~116 мс».

## Если что-то пошло не так
| Проблема | Что делать |
|---|---|
| API не отвечает | `$C ps`, `$C logs api --tail 20`, `$C up -d` |
| Пустые графики | нагнать трафик (см. подготовку), подождать 15 с |
| Алерт не пришёл вовремя | показать вкладку Alerting → Pending/Firing, объяснить `for: 2m` |
| Забыли поднять БД | `$C start postgres`, через 5 с `/ready` = 200 |
