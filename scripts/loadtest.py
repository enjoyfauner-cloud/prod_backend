"""Нагрузочный тест по сети: параллельные HTTP-запросы к запущенному API.

    python -m scripts.loadtest --url http://localhost:8000 --concurrency 1 10 50 --duration 15

Для каждого уровня параллельности в течение --duration секунд крутит запросы
POST /api/v1/recommendations по разным категориям и печатает RPS, p50/p95/p99 и
долю ошибок. Заодно проверяет, что под нагрузкой ответы не меняются (детерминизм).
"""

import argparse
import asyncio
import statistics
import time

import httpx

CATEGORIES = ["supermarkets", "restaurants", "fuel", "taxi", "pharmacy", "online", "other"]
AS_OF = "2026-11-20"


def body(i: int) -> dict[str, str]:
    return {"category": CATEGORIES[i % len(CATEGORIES)], "amount": "2500.00", "as_of": AS_OF}


async def run_level(
    client: httpx.AsyncClient, concurrency: int, duration: float, reference: dict[str, object]
) -> None:
    latencies: list[float] = []
    errors = mismatches = 0
    deadline = time.perf_counter() + duration

    async def worker(wid: int) -> None:
        nonlocal errors, mismatches
        i = wid
        while time.perf_counter() < deadline:
            payload = body(i)
            t0 = time.perf_counter()
            try:
                resp = await client.post("/api/v1/recommendations", json=payload)
                latencies.append((time.perf_counter() - t0) * 1000)
                if resp.status_code != 200:
                    errors += 1
                elif resp.json() != reference[payload["category"]]:
                    mismatches += 1
            except httpx.HTTPError:
                errors += 1
            i += concurrency

    started = time.perf_counter()
    await asyncio.gather(*(worker(w) for w in range(concurrency)))
    elapsed = time.perf_counter() - started
    total = len(latencies) + (errors if errors > len(latencies) else 0)
    q = statistics.quantiles(latencies, n=100) if len(latencies) > 1 else [0.0] * 99
    print(
        f"параллельно {concurrency:>3}: {total / elapsed:7.1f} RPS | "
        f"p50 {q[49]:6.1f} мс  p95 {q[94]:6.1f} мс  p99 {q[98]:6.1f} мс | "
        f"ошибок {errors}  расхождений {mismatches}  (запросов {total})"
    )


async def main(url: str, levels: list[int], duration: float) -> None:
    limits = httpx.Limits(max_connections=max(levels), max_keepalive_connections=max(levels))
    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=30) as client:
        ready = await client.get("/ready")
        ready.raise_for_status()
        # эталонные ответы: под нагрузкой должны быть ровно такими же
        reference = {}
        for i, cat in enumerate(CATEGORIES):
            reference[cat] = (await client.post("/api/v1/recommendations", json=body(i))).json()
        print(f"{url}, {duration:.0f} с на уровень, категории: {len(CATEGORIES)}")
        for level in levels:
            await run_level(client, level, duration, reference)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 10, 50])
    parser.add_argument("--duration", type=float, default=15)
    args = parser.parse_args()
    asyncio.run(main(args.url, args.concurrency, args.duration))
