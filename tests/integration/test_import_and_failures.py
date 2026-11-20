"""Идемпотентность импорта и поведение при сбое БД."""

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import Settings
from app.main import create_app
from app.schemas.catalog_file import CatalogFile
from app.services.catalog_import import CatalogImportError, import_catalog
from tests.integration.conftest import DEMO

COUNTS = (
    "SELECT (SELECT count(*) FROM banks), (SELECT count(*) FROM cards), "
    "(SELECT count(*) FROM card_terms), (SELECT count(*) FROM cashback_rules)"
)


async def test_reimport_creates_no_duplicates(app: FastAPI) -> None:
    async with app.state.engine.connect() as conn:
        before = (await conn.execute(text(COUNTS))).one()
    for _ in range(2):
        async with app.state.sessionmaker() as session, session.begin():
            await import_catalog(session, DEMO)
    async with app.state.engine.connect() as conn:
        after = (await conn.execute(text(COUNTS))).one()
    assert before == after == (6, 50, 60, 168)


async def test_reimport_updates_terms_in_place(app: FastAPI, client: AsyncClient) -> None:
    data = DEMO.model_dump(mode="json")
    semya = next(c for c in data["cards"] if c["code"] == "bank-yug-semya")
    semya["terms"][1]["rules"] = [{"category": "supermarkets", "rate_percent": "9.00"}]
    async with app.state.sessionmaker() as session, session.begin():
        await import_catalog(session, CatalogFile.model_validate(data))
    detail = (await client.get("/api/v1/cards/bank-yug-semya")).json()
    assert [r["rate_percent"] for r in detail["terms"]["rules"]] == ["9.00"]


async def test_import_with_unknown_category_is_atomic(app: FastAPI) -> None:
    data = DEMO.model_dump(mode="json")
    data["cards"][0]["terms"][0]["rules"].append({"category": "casino", "rate_percent": "1"})
    try:
        async with app.state.sessionmaker() as session, session.begin():
            await import_catalog(session, CatalogFile.model_validate(data))
    except CatalogImportError:
        pass
    else:
        raise AssertionError("ожидали CatalogImportError")
    async with app.state.engine.connect() as conn:
        assert (await conn.execute(text(COUNTS))).one() == (6, 50, 60, 168)


async def test_db_down_gives_503_not_500() -> None:
    settings = Settings(
        app_env="test",
        database_url="postgresql+asyncpg://nobody:nobody@127.0.0.1:1/none",
        log_level="CRITICAL",
        catalog_gauge_interval_seconds=3600,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            assert (await ac.get("/health")).status_code == 200  # liveness не зависит от БД
            ready = await ac.get("/ready")
            assert ready.status_code == 503
            assert ready.json()["error"]["code"] == "service_unavailable"
            resp = await ac.get("/api/v1/categories")
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "service_unavailable"
            assert resp.headers["X-Request-Id"]
