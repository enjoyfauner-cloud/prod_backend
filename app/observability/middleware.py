"""Middleware: request_id, access-лог в JSON, HTTP-метрики."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.routing import Match

from app.errors import error_response
from app.observability.context import set_request_id
from app.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = logging.getLogger("app.access")

_MAX_REQUEST_ID_LEN = 64


def _route_template(request: Request) -> str:
    """Шаблон пути (/api/v1/cards/{card_id}), чтобы не раздувать кардинальность метрик."""
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", "unmatched")
    return "unmatched"


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming[:_MAX_REQUEST_ID_LEN] if incoming else uuid.uuid4().hex
        set_request_id(request_id)
        route = _route_template(request)
        start = time.perf_counter()
        status = 500
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Последний рубеж: клиент получает единый формат ошибки и X-Request-Id.
                logger.exception("unhandled error")
                response = error_response(500, "internal_error", "Внутренняя ошибка сервера")
            status = response.status_code
        finally:
            elapsed = time.perf_counter() - start
            if route != "/metrics":
                HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
                HTTP_LATENCY.labels(request.method, route).observe(elapsed)
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "route": route,
                    "status": status,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
        response.headers["X-Request-Id"] = request_id
        return response
