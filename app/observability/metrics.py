"""Prometheus-метрики. Метки только с ограниченным набором значений (шаблон роута, а не URL).

При нескольких процессах uvicorn (WEB_CONCURRENCY > 1) включается multiprocess-режим
prometheus_client: переменная PROMETHEUS_MULTIPROC_DIR, каждый процесс пишет свои значения
в файлы, а /metrics суммирует их (см. collect_metrics).
"""

import os

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    multiprocess,
)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP-запросы",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Длительность обработки HTTP-запроса",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
RECOMMENDATION_CALC = Histogram(
    "recommendation_calc_seconds",
    "Время чистого расчёта рекомендаций (без БД)",
    ["scope"],
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
)
RECOMMENDATIONS = Counter(
    "recommendations_total",
    "Запросы рекомендаций",
    ["scope", "category"],
)
CATALOG_CARDS = Gauge(
    "catalog_cards_total",
    "Число карт в каталоге",
    ["status"],
    multiprocess_mode="max",  # при нескольких воркерах берём максимум, а не сумму
)
APP_ERRORS = Counter(
    "app_errors_total",
    "Ошибки, отданные клиенту, по коду",
    ["code"],
)


def collect_metrics() -> bytes:
    """Метрики всех процессов (multiprocess) или текущего (один процесс, тесты)."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return generate_latest(registry)
    return generate_latest(REGISTRY)
