# API-контракт «Советник» v1

Документ для frontend. Живая схема лежит в Swagger (`/docs`). Если они разойдутся, источником правды считается OpenAPI, а этот файл я буду обновлять.

## Как подключиться к backend

Публичного сервера нет, backend запускается в Docker. Есть два варианта:

**А. Поднять backend у себя (удобно для разработки).** Нужны Docker и git:
```bash
git clone https://github.com/enjoyfauner-cloud/prod_backend.git && cd prod_backend
docker compose up --build -d
# API:      http://localhost:8000/api/v1   Swagger: http://localhost:8000/docs
```
По умолчанию CORS разрешён для `http://localhost:3000` и `http://localhost:5173`. Если dev-сервер на другом порту, запустите так:
`CORS_ORIGINS=http://localhost:4200 docker compose up -d api`.

**Б. Ходить в backend на ноутбуке напарника по одной Wi-Fi-сети (для защиты).** Базовый URL `http://<IP ноутбука>:8000/api/v1`. Свой адрес (например, `http://192.168.1.23:5173`) пришли мне, я добавлю его в `CORS_ORIGINS`.

Базовый URL лучше вынести в переменную окружения фронтенда (`VITE_API_URL` / `NEXT_PUBLIC_API_URL`), а не зашивать в код.

## Общее

- Базовый путь: `/api/v1`. Формат запросов и ответов: JSON, UTF-8.
- **Деньги передаются строкой** с двумя знаками: `"2500.00"`, `"125.00"`. В запросе можно прислать число или строку, в ответе всегда строка. Не превращайте их во float для вычислений: для показа достаточно строки.
- Проценты тоже передаются строкой: `"5.00"` означает 5 %.
- Даты имеют формат `YYYY-MM-DD`.
- CORS: домен фронтенда задаётся в конфиге бэкенда (`CORS_ORIGINS`). Пришли мне адрес dev-сервера и продакшен-домен.
- Сообщения об ошибках (`message`) на русском, их можно показывать пользователю как есть.
- `X-Request-Id`: можно присылать свой, иначе сервер сгенерирует. Он всегда возвращается в заголовке ответа. Полезно при багрепортах.

### Идентификация пользователя («Мои карты»)
Регистрации нет. При первом заходе фронтенд генерирует UUID v4 (`crypto.randomUUID()`), сохраняет его в `localStorage` и шлёт в каждом запросе к `/me/*` и в рекомендациях с `scope: "my"`:
```
X-User-Id: 3f1c2a9e-8b7d-4c3e-9f10-2a4b6c8d0e1f
```
Если заголовок отсутствует или это не UUID, сервер вернёт `400 invalid_user_id`.
**Номер карты, CVV, SMS-коды и любые реквизиты не запрашиваем и не отправляем.** Хранится только ссылка на карту из каталога.

### Формат ошибок (для всех эндпоинтов)
```json
{
  "error": {
    "code": "validation_error",
    "message": "Сумма должна быть больше 0",
    "details": [{"field": "amount", "message": "Сумма должна быть больше 0"}],
    "request_id": "b7e1…"
  }
}
```
| HTTP | code | Когда |
|---|---|---|
| 400 | `invalid_user_id` | нет `X-User-Id` или он не UUID |
| 404 | `card_not_found` | карты нет в каталоге или в «моих картах» |
| 404 | `not_found` | неизвестный путь |
| 409 | `card_already_added` | карта уже есть в «моих картах» |
| 422 | `validation_error` | неверное тело или параметры (`details` по полям) |
| 422 | `unknown_category` | категории нет в справочнике |
| 500 | `internal_error` | сбой сервера (детали только в логах, по `request_id`) |
| 503 | `service_unavailable` | БД недоступна |

---

## Категории

### `GET /api/v1/categories`
```json
[
  {"code": "supermarkets", "name": "Супермаркеты"},
  {"code": "restaurants",  "name": "Рестораны"},
  {"code": "fuel",         "name": "АЗС"},
  {"code": "taxi",         "name": "Такси"},
  {"code": "pharmacy",     "name": "Аптеки"},
  {"code": "online",       "name": "Онлайн-покупки"},
  {"code": "other",        "name": "Всё остальное"}
]
```
Порядок фиксирован, его можно использовать прямо для выпадающего списка.

---

## Рекомендации

### `POST /api/v1/recommendations`
Запрос:
```json
{
  "category": "supermarkets",
  "amount": "2500.00",
  "scope": "catalog",
  "top": 3,
  "as_of": "2026-11-20"
}
```
| Поле | Обяз. | Описание |
|---|---|---|
| `category` | да | код категории из `/categories` |
| `amount` | да | `> 0` и `≤ 10 000 000`, не больше 2 знаков после запятой |
| `scope` | нет | `"catalog"` (по умолчанию) или `"my"`. Для `"my"` обязателен `X-User-Id` |
| `top` | нет | 1–20, по умолчанию 3 |
| `as_of` | нет | дата, на которую считать условия. По умолчанию «сегодня» (МСК). Обычно не нужен |

Ответ `200`:
```json
{
  "category": {"code": "supermarkets", "name": "Супермаркеты"},
  "amount": "2500.00",
  "scope": "catalog",
  "as_of": "2026-11-20",
  "rounding": "down_to_kopeck",
  "top": [ /* первые N элементов eligible */ ],
  "eligible": [
    {
      "rank": 1,
      "card": {"id": "bank-sever-cashback-plus", "name": "Кэшбэк Плюс", "bank": {"code": "bank-sever", "name": "Банк Север"}},
      "cashback": "125.00",
      "effective_rate_percent": "5.00",
      "breakdown": {
        "rule_category": "supermarkets",
        "rule_type": "category",
        "fallback_reason": null,
        "rate_percent": "5.00",
        "raw_cashback": "125.00",
        "monthly_limit": "3000.00",
        "limit_used_before": "0.00",
        "limit_remaining_before": "3000.00",
        "limit_applied": false,
        "min_purchase": null,
        "explanation": "2500.00 × 5.00% = 125.00 ₽; лимит 3000.00 ₽ не достигнут"
      },
      "terms": {
        "version": 2,
        "valid_from": "2026-10-01",
        "valid_to": "2026-12-31",
        "checked_at": "2026-11-15",
        "is_current": true
      },
      "promo_url": "https://example.com/apply"
    }
  ],
  "outdated": [ /* такие же элементы, terms.is_current = false, rank = null */ ],
  "not_eligible_count": 4
}
```
Пояснения:
- `top` содержит первые `top` элементов `eligible`. `eligible` даёт **полный список подходящих карт** в том же порядке.
- `breakdown.rule_type`: `"category"` (ставка категории) или `"base"` (взята ставка «Всё остальное»). `fallback_reason` объясняет откат: `"no_category_rule"` (у карты нет условия для категории) или `"below_min_purchase"` (сумма меньше минимальной).
- `limit_applied: true` означает, что кэшбэк упёрся в лимит. Тогда `cashback < raw_cashback`.
- `monthly_limit: null` означает, что лимита нет.
- `explanation` содержит готовую строку «как посчитано» для показа пользователю.
- **`outdated`**: карты, условия которых истекли к `as_of`. Они **никогда не попадают в `top`/`eligible`**. Показывайте их с пометкой «условия устарели, действовали до `terms.valid_to`».
- **Всегда показывайте `terms.valid_from`–`valid_to` и `checked_at`** («условия проверены 15.11.2026»). Этого требует кейс.
- `promo_url` нужен только для кнопки «Оформить». На порядок он не влияет.
- Повторный одинаковый запрос (с тем же `as_of`) даёт байт-в-байт одинаковый результат.
- `scope: "my"`: считаются только карты пользователя, с учётом использованного лимита за месяц `as_of`. Если карт нет, списки пустые и `not_eligible_count: 0`.

Порядок при равном кэшбэке: больше остаток лимита после покупки → дольше действуют условия → `card.id` по алфавиту.

---

## Каталог карт

### `GET /api/v1/cards`
Параметры query (все необязательные):
| Параметр | Описание |
|---|---|
| `category` | только карты, у которых есть правило этой категории |
| `bank` | код банка |
| `current_only` | `true` (по умолчанию): только карты с действующими условиями; `false`: все |
| `limit` | 1–200, по умолчанию 50 |
| `offset` | по умолчанию 0 |

Ответ `200`:
```json
{
  "items": [
    {
      "id": "bank-sever-cashback-plus",
      "name": "Кэшбэк Плюс",
      "bank": {"code": "bank-sever", "name": "Банк Север"},
      "terms": {"version": 2, "valid_from": "2026-10-01", "valid_to": "2026-12-31", "checked_at": "2026-11-15", "is_current": true},
      "categories": ["supermarkets", "restaurants", "other"],
      "promo_url": "https://example.com/apply"
    }
  ],
  "total": 48,
  "limit": 50,
  "offset": 0
}
```
Сортировка: банк → название (стабильная). `terms` может быть `null`, если у карты есть только будущие условия.

### `GET /api/v1/cards/{card_id}`
Ответ `200`: подробные условия карты.
```json
{
  "id": "bank-sever-cashback-plus",
  "name": "Кэшбэк Плюс",
  "description": "…",
  "bank": {"code": "bank-sever", "name": "Банк Север"},
  "promo_url": "https://example.com/apply",
  "is_demo_data": true,
  "terms": {
    "version": 2, "valid_from": "2026-10-01", "valid_to": "2026-12-31",
    "checked_at": "2026-11-15", "is_current": true,
    "rules": [
      {"category": {"code": "supermarkets", "name": "Супермаркеты"}, "rate_percent": "5.00", "monthly_limit": "3000.00", "min_purchase": null},
      {"category": {"code": "other", "name": "Всё остальное"}, "rate_percent": "1.00", "monthly_limit": null, "min_purchase": null}
    ]
  }
}
```
`terms` содержит действующую версию. Если её нет, отдаётся последняя истёкшая с `is_current: false`. `404 card_not_found`, если карты нет.

---

## Мои карты (нужен `X-User-Id`)

### `GET /api/v1/me/cards`
```json
{
  "items": [
    {
      "card": { /* как элемент из GET /cards */ },
      "added_at": "2026-11-20T10:15:00Z",
      "usage": [
        {"category": "supermarkets", "period": "2026-11", "used_cashback": "450.00"}
      ]
    }
  ]
}
```
`usage` относится только к текущему месяцу. Порядок: по времени добавления.

### `POST /api/v1/me/cards`
Тело: `{"card_id": "bank-sever-cashback-plus"}` → `201` с элементом как в списке.
Ошибки: `404 card_not_found`, `409 card_already_added`.

### `DELETE /api/v1/me/cards/{card_id}`
`204`. Использование по этой карте тоже удаляется. Если карты нет в «моих», ответ `404 card_not_found`.

### `PUT /api/v1/me/cards/{card_id}/usage`
Указать, сколько кэшбэка по категории уже получено в этом месяце. Повторный вызов перезаписывает значение (идемпотентно).
```json
{"category": "supermarkets", "used_cashback": "450.00", "period": "2026-11"}
```
`period` необязателен, по умолчанию текущий месяц. `used_cashback ≥ 0`. Чтобы сбросить, пришлите `"0.00"`.
Ответ `200`: `{"category": "supermarkets", "period": "2026-11", "used_cashback": "450.00"}`.
Ошибки: `404 card_not_found` (карты нет в «моих»), `422 unknown_category`.

---

## Служебные (без `/api/v1`)
| Метод | Путь | Ответ |
|---|---|---|
| GET | `/health` | `200 {"status": "ok"}`: процесс жив |
| GET | `/ready` | `200 {"status": "ready"}` или `503`, если БД недоступна |
| GET | `/metrics` | метрики Prometheus (text) |
| GET | `/docs` | Swagger UI |
| GET | `/openapi.json` | схема OpenAPI |

## Важно для UI
- Каталог **демонстрационный**: банки и условия вымышлены. В деталях карты есть `is_demo_data: true`, покажите плашку.
- Показывайте даты действия условий рядом с суммой кэшбэка.
