"""Интеграционные тесты: настоящее приложение + настоящий PostgreSQL.

БД берётся из TEST_DATABASE_URL (по умолчанию sovetnik_test из docker-compose).
Схема создаётся миграциями Alembic (downgrade base → upgrade head), поэтому
каждый прогон заодно проверяет миграции в обе стороны.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from alembic.config import Config
from app.config import Settings
from app.main import create_app
from app.services.catalog_import import import_catalog, load_catalog_file

ROOT = Path(__file__).resolve().parents[2]
TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://sovetnik:sovetnik@localhost:5433/sovetnik_test"
)
DEMO = load_catalog_file(ROOT / "data" / "demo_catalog.json")
AS_OF = "2026-11-20"

pytestmark = pytest.mark.integration


async def _ping() -> None:
    engine = create_async_engine(TEST_DB_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> None:
    try:
        asyncio.run(_ping())
    except Exception as exc:
        pytest.fail(
            f"PostgreSQL для тестов недоступен ({TEST_DB_URL}): {exc!r}. "
            "Запустите: docker compose up -d postgres"
        )
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["database_url"] = TEST_DB_URL
    cfg.attributes["skip_logging"] = True
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        database_url=TEST_DB_URL,
        log_level="WARNING",
        catalog_gauge_interval_seconds=3600,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    """Приложение с чистой БД и загруженным демо-каталогом."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE card_usage, user_cards, cashback_rules, card_terms, cards, banks "
                    "RESTART IDENTITY CASCADE"
                )
            )
        async with app.state.sessionmaker() as session, session.begin():
            await import_catalog(session, DEMO)
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
