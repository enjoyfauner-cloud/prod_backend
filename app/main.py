"""Точка входа: сборка FastAPI-приложения."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import catalog, health, me, recommendations
from app.api.deps import today
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.errors import install_error_handlers
from app.observability.logging import configure_logging
from app.observability.metrics import CATALOG_CARDS
from app.observability.middleware import install_middleware
from app.services import repository as repo

logger = logging.getLogger("app")
APP_VERSION = os.environ.get("APP_VERSION", "dev")


async def refresh_catalog_gauge(app: FastAPI, settings: Settings) -> None:
    """Раз в N секунд обновляет метрику размера каталога. Сбой не роняет приложение."""
    while True:
        try:
            async with app.state.sessionmaker() as session:
                total, current = await repo.count_cards(session, today(settings))
            CATALOG_CARDS.labels("total").set(total)
            CATALOG_CARDS.labels("current").set(current)
        except Exception as exc:
            logger.warning("catalog gauge refresh failed", extra={"error": repr(exc)})
        await asyncio.sleep(settings.catalog_gauge_interval_seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = make_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        logger.info("startup", extra={"env": settings.app_env, "version": APP_VERSION})
        gauge_task = asyncio.create_task(refresh_catalog_gauge(app, settings))
        try:
            yield
        finally:
            gauge_task.cancel()
            with suppress(asyncio.CancelledError):
                await gauge_task  # дождаться, пока задача отпустит соединение
            await engine.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title="Советник: какую карту выбрать для покупки",
        version=APP_VERSION,
        description=(
            "Сравнивает условия кэшбэка банковских карт и рекомендует самые выгодные. "
            "Каталог демонстрационный, банки вымышлены."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    install_middleware(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-User-Id", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(catalog.router)
    app.include_router(recommendations.router)
    app.include_router(me.router)
    return app
