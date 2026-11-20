"""Идемпотентность импорта и поведение при сбое БД."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

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


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1:1", "localhost:1", "no-such-host.invalid:5432"],  # отказ, IPv4+IPv6, DNS
)
async def test_db_down_gives_503_not_500(host: str) -> None:
    settings = Settings(
        app_env="test",
        database_url=f"postgresql+asyncpg://nobody:nobody@{host}/none",
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
            metrics = (await ac.get("/metrics")).text
            assert 'app_errors_total{code="service_unavailable"}' in metrics


# ---------- регрессии из код-ревью ----------


async def _reimport(app: FastAPI, data: dict[str, object]) -> None:
    async with app.state.sessionmaker() as session, session.begin():
        await import_catalog(session, CatalogFile.model_validate(data))


def _card(data: dict, code: str) -> dict:  # type: ignore[type-arg]
    return next(c for c in data["cards"] if c["code"] == code)


async def test_rename_and_move_card_keeps_identity(app: FastAPI, client: AsyncClient) -> None:
    headers = {"X-User-Id": "3f1c2a9e-8b7d-4c3e-9f10-2a4b6c8d0e1f"}
    await client.post("/api/v1/me/cards", json={"card_id": "bank-zapad-vsyo"}, headers=headers)
    data = DEMO.model_dump(mode="json")
    _card(data, "bank-zapad-vsyo").update(name="Всё и сразу 2.0", bank="bank-reka")
    await _reimport(app, data)
    detail = (await client.get("/api/v1/cards/bank-zapad-vsyo")).json()
    assert detail["name"] == "Всё и сразу 2.0"
    assert detail["bank"]["code"] == "bank-reka"
    async with app.state.engine.connect() as conn:
        assert (await conn.execute(text(COUNTS))).one() == (6, 50, 60, 168)
    mine = (await client.get("/api/v1/me/cards", headers=headers)).json()["items"]
    assert [i["card"]["id"] for i in mine] == ["bank-zapad-vsyo"]  # ссылка не потерялась


async def test_name_clash_with_other_card_is_clean_error(app: FastAPI) -> None:
    # В БД уже есть bank-sever-produkty «Продуктовая». Новый файл её не содержит,
    # зато заводит другую карту (другой code) с тем же названием у того же банка.
    data = DEMO.model_dump(mode="json")
    produkty = _card(data, "bank-sever-produkty")
    data["cards"] = [c for c in data["cards"] if c["code"] != "bank-sever-produkty"]
    data["cards"].append({**produkty, "code": "bank-sever-new"})
    try:
        await _reimport(app, data)
    except CatalogImportError as exc:
        assert "одинаковым названием" in str(exc)
    else:
        raise AssertionError("ожидали CatalogImportError")
    async with app.state.engine.connect() as conn:
        assert (await conn.execute(text(COUNTS))).one() == (6, 50, 60, 168)


async def test_versions_removed_from_file_are_deleted(app: FastAPI, client: AsyncClient) -> None:
    data = DEMO.model_dump(mode="json")
    semya = _card(data, "bank-yug-semya")
    # было: v1 (янв–июн) + v2; станет: только бессрочная v3 с 01.10
    semya["terms"] = [
        {**semya["terms"][1], "version": 3, "valid_from": "2026-10-01", "valid_to": None}
    ]
    await _reimport(app, data)
    async with app.state.engine.connect() as conn:
        versions = (
            (
                await conn.execute(
                    text(
                        "SELECT t.version FROM card_terms t JOIN cards c ON c.id = t.card_id "
                        "WHERE c.code = 'bank-yug-semya'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert versions == [3]
    assert (await client.get("/api/v1/cards/bank-yug-semya")).json()["terms"]["version"] == 3


async def test_category_filter_uses_displayed_version(app: FastAPI, client: AsyncClient) -> None:
    data = DEMO.model_dump(mode="json")
    # у старой (истёкшей) v1 «Семейной» есть АЗС, у действующей v2 — нет
    _card(data, "bank-yug-semya")["terms"][0]["rules"].append(
        {"category": "fuel", "rate_percent": "3.00"}
    )
    await _reimport(app, data)
    params = {"category": "fuel", "current_only": "false", "limit": 200}
    fuel = (await client.get("/api/v1/cards", params=params)).json()["items"]
    assert "bank-yug-semya" not in {c["id"] for c in fuel}
    assert all("fuel" in c["categories"] for c in fuel)
    # истёкшая карта ищется по своей последней версии
    params = {"category": "supermarkets", "current_only": "false", "limit": 200}
    supermarkets = (await client.get("/api/v1/cards", params=params)).json()["items"]
    assert "bank-gorny-arhiv" in {c["id"] for c in supermarkets}
    params["current_only"] = "true"
    current = (await client.get("/api/v1/cards", params=params)).json()["items"]
    assert "bank-gorny-arhiv" not in {c["id"] for c in current}


async def test_non_connection_db_error_is_500_not_503(settings: Settings) -> None:
    from sqlalchemy.exc import IntegrityError

    app = create_app(settings)

    async def boom() -> None:
        raise IntegrityError("INSERT ...", {}, Exception("duplicate key"))

    app.add_api_route("/boom", boom)
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://t") as ac,
    ):
        resp = await ac.get("/boom")
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"


async def test_cards_can_swap_names_in_one_import(app: FastAPI, client: AsyncClient) -> None:
    data = DEMO.model_dump(mode="json")
    a, b = _card(data, "bank-sever-produkty"), _card(data, "bank-sever-reklama")
    a["name"], b["name"] = b["name"], a["name"]
    await _reimport(app, data)
    assert (await client.get("/api/v1/cards/bank-sever-produkty")).json()["name"] == "Рекламная"
    assert (await client.get("/api/v1/cards/bank-sever-reklama")).json()["name"] == "Продуктовая"


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (SQLAlchemyTimeoutError("QueuePool limit reached"), 503, "service_unavailable"),
        (FileNotFoundError(2, "No such file"), 500, "internal_error"),
        (PermissionError(13, "Permission denied"), 500, "internal_error"),
        (ConnectionRefusedError(61, "refused"), 503, "service_unavailable"),
    ],
)
async def test_error_classification_via_http(
    settings: Settings, exc: Exception, status: int, code: str
) -> None:
    app = create_app(settings)

    async def boom() -> None:
        raise exc

    app.add_api_route("/boom", boom)
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://t") as ac,
    ):
        resp = await ac.get("/boom")
    assert (resp.status_code, resp.json()["error"]["code"]) == (status, code)
