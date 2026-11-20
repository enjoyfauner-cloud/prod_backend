"""Точка входа: сборка FastAPI-приложения."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.errors import install_error_handlers
from app.observability.logging import configure_logging
from app.observability.middleware import install_middleware

logger = logging.getLogger("app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = make_engine(settings)
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        logger.info("startup", extra={"env": settings.app_env})
        try:
            yield
        finally:
            await engine.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title="Советник: какую карту выбрать для покупки",
        version="1.0.0",
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
    return app
