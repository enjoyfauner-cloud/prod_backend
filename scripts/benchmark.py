"""Проверка производительности на большом каталоге (по умолчанию 600 случайных + 8 витринных карт).

    python -m scripts.benchmark --db postgresql+asyncpg://sovetnik:sovetnik@localhost:5433/sovetnik_test

ВНИМАНИЕ: очищает таблицы каталога в указанной БД. Используйте тестовую БД.
Запросы идут в настоящее приложение (ASGI в процессе, без сети), с настоящим PostgreSQL.
"""

import argparse
import asyncio
import statistics
import time
from datetime import date
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.services import repository as repo
from app.services.calculator import recommend
from app.services.catalog_import import import_catalog
from scripts.generate_catalog import build

CATEGORIES = ["supermarkets", "restaurants", "fuel", "taxi", "pharmacy", "online", "other"]
AS_OF = date(2026, 11, 20)


def pct(values: list[float], q: float) -> float:
    return statistics.quantiles(values, n=100)[int(q) - 1] if len(values) > 1 else values[0]


async def main(db_url: str, count: int, requests: int) -> None:
    settings = Settings(
        database_url=db_url, log_level="WARNING", catalog_gauge_interval_seconds=3600
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE card_usage, user_cards, cashback_rules, card_terms, cards, banks "
                    "RESTART IDENTITY CASCADE"
                )
            )
        catalog = build(count, seed=7)
        started = time.perf_counter()
        async with app.state.sessionmaker() as session, session.begin():
            report = await import_catalog(session, catalog)
        print(
            f"Импорт: {report.cards} карт, {report.rules} правил "
            f"за {time.perf_counter() - started:.2f} с"
        )
        started = time.perf_counter()
        async with app.state.sessionmaker() as session, session.begin():
            await import_catalog(session, catalog)
        print(f"Повторный импорт (идемпотентный): {time.perf_counter() - started:.2f} с")

        # 1) чистый расчёт без БД
        async with app.state.sessionmaker() as session:
            offers = await repo.load_offers(session, "supermarkets", AS_OF)
        calc_times = []
        for _ in range(200):
            t0 = time.perf_counter()
            recommend(offers, "supermarkets", Decimal("2500.00"), AS_OF)
            calc_times.append((time.perf_counter() - t0) * 1000)
        print(
            f"Чистый расчёт по {len(offers)} картам: p50 {pct(calc_times, 50):.2f} мс, "
            f"p95 {pct(calc_times, 95):.2f} мс"
        )

        # 2) полный HTTP-запрос (валидация + SQL + расчёт + сериализация)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://bench") as client:
            latencies: list[float] = []
            first: dict[str, object] | None = None
            for i in range(requests):
                body = {
                    "category": CATEGORIES[i % len(CATEGORIES)],
                    "amount": "2500.00",
                    "as_of": AS_OF.isoformat(),
                }
                t0 = time.perf_counter()
                resp = await client.post("/api/v1/recommendations", json=body)
                latencies.append((time.perf_counter() - t0) * 1000)
                assert resp.status_code == 200, resp.text
                if i == 0:
                    first = resp.json()
            # воспроизводимость на большом каталоге
            again = await client.post(
                "/api/v1/recommendations",
                json={"category": "supermarkets", "amount": "2500.00", "as_of": AS_OF.isoformat()},
            )
            assert again.json() == first, "повторный запрос дал другой результат"
            eligible = len(again.json()["eligible"])
        print(
            f"HTTP POST /recommendations ×{requests}: p50 {pct(latencies, 50):.1f} мс, "
            f"p95 {pct(latencies, 95):.1f} мс, max {max(latencies):.1f} мс "
            f"(подходящих карт в ответе: {eligible})"
        )
        print("Повторный одинаковый запрос: результат идентичен ✓")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", default="postgresql+asyncpg://sovetnik:sovetnik@localhost:5433/sovetnik_test"
    )
    parser.add_argument("--count", type=int, default=600)
    parser.add_argument("--requests", type=int, default=300)
    args = parser.parse_args()
    asyncio.run(main(args.db, args.count, args.requests))
