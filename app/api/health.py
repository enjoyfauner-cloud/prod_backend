"""Служебные эндпоинты: liveness, readiness, метрики."""

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.errors import error_body

router = APIRouter(tags=["service"])
logger = logging.getLogger("app.health")


@router.get("/health", summary="Liveness: процесс жив")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness: БД доступна",
    responses={503: {"description": "БД недоступна"}},
)
async def ready(request: Request) -> Response:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error": repr(exc)})
        return JSONResponse(
            status_code=503,
            content=error_body("service_unavailable", "База данных недоступна"),
        )
    return JSONResponse({"status": "ready"})


@router.get("/metrics", summary="Метрики Prometheus", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
