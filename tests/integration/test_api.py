"""Интеграционные тесты HTTP API — сценарии из критериев готовности MVP."""

import uuid
from typing import Any

from httpx import AsyncClient

from tests.integration.conftest import AS_OF

USER = str(uuid.UUID("3f1c2a9e-8b7d-4c3e-9f10-2a4b6c8d0e1f"))
H = {"X-User-Id": USER}


async def recommend(client: AsyncClient, **body: Any) -> dict[str, Any]:
    payload = {"category": "supermarkets", "amount": "2500.00", "as_of": AS_OF, **body}
    resp = await client.post("/api/v1/recommendations", json=payload, headers=H)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------- служебные ----------


async def test_health_ready_metrics(client: AsyncClient) -> None:
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}
    metrics = await client.get("/metrics")
    assert "http_requests_total" in metrics.text


async def test_request_id_roundtrip(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"X-Request-Id": "abc-123"})
    assert resp.headers["X-Request-Id"] == "abc-123"
    assert len((await client.get("/health")).headers["X-Request-Id"]) == 32


async def test_openapi_available(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert {"/api/v1/recommendations", "/api/v1/cards/{card_id}", "/api/v1/me/cards"} <= set(paths)


# ---------- критерии MVP ----------


async def test_categories(client: AsyncClient) -> None:
    data = (await client.get("/api/v1/categories")).json()
    assert data[0] == {"code": "supermarkets", "name": "Супермаркеты"}
    assert data[-1]["code"] == "other"
    assert len(data) == 7


async def test_supermarkets_2500_top3_in_rubles(client: AsyncClient) -> None:
    data = await recommend(client)
    top = [(i["card"]["id"], i["cashback"]) for i in data["top"]]
    assert top == [
        ("bank-sever-produkty", "150.00"),
        ("bank-reka-dvoyka", "125.00"),
        ("bank-yug-semya", "125.00"),
    ]
    first = data["top"][0]
    assert first["rank"] == 1
    assert first["breakdown"]["limit_applied"] is True
    assert first["breakdown"]["raw_cashback"] == "175.00"
    assert "лимит" in first["breakdown"]["explanation"]
    assert first["terms"]["checked_at"] == "2026-11-15"
    assert first["terms"]["is_current"] is True
    assert data["rounding"] == "down_to_kopeck"
    assert data["amount"] == "2500.00"


async def test_full_eligible_list_and_outdated(client: AsyncClient) -> None:
    data = await recommend(client)
    assert len(data["eligible"]) > 3
    assert data["eligible"][:3] == data["top"]
    assert [i["rank"] for i in data["eligible"]] == list(range(1, len(data["eligible"]) + 1))
    eligible_ids = {i["card"]["id"] for i in data["eligible"]}
    outdated_ids = {i["card"]["id"] for i in data["outdated"]}
    assert "bank-gorny-arhiv" in outdated_ids
    assert not eligible_ids & outdated_ids
    assert all(not i["terms"]["is_current"] and i["rank"] is None for i in data["outdated"])
    assert "bank-vostok-avto" not in eligible_ids | outdated_ids
    assert data["not_eligible_count"] >= 1
    total = len(eligible_ids) + len(outdated_ids) + data["not_eligible_count"]
    assert total == 50


async def test_fallbacks_visible_in_breakdown(client: AsyncClient) -> None:
    items = {i["card"]["id"]: i for i in (await recommend(client))["eligible"]}
    krupnaya = items["bank-vostok-krupnaya"]["breakdown"]
    assert krupnaya["fallback_reason"] == "below_min_purchase"
    assert items["bank-vostok-krupnaya"]["cashback"] == "37.50"
    vsyo = items["bank-zapad-vsyo"]["breakdown"]
    assert vsyo["fallback_reason"] == "no_category_rule"
    assert vsyo["rule_type"] == "base"


async def test_other_category_changes_result(client: AsyncClient) -> None:
    supermarkets = await recommend(client)
    fuel = await recommend(client, category="fuel")
    assert supermarkets["top"] != fuel["top"]
    assert fuel["category"] == {"code": "fuel", "name": "АЗС"}


async def test_repeat_request_is_identical(client: AsyncClient) -> None:
    first = await recommend(client)
    for _ in range(3):
        assert await recommend(client) == first


async def test_top_parameter(client: AsyncClient) -> None:
    assert len((await recommend(client, top=1))["top"]) == 1
    assert len((await recommend(client, top=10))["top"]) == 10


async def test_as_of_after_expiry_moves_card_to_outdated(client: AsyncClient) -> None:
    before = await recommend(client, as_of="2026-09-15")
    assert before["top"][0]["card"]["id"] == "bank-gorny-arhiv"  # 10% ещё действовали
    after = await recommend(client)
    assert all(i["card"]["id"] != "bank-gorny-arhiv" for i in after["eligible"])


# ---------- валидация и ошибки ----------


async def post_raw(
    client: AsyncClient, body: dict[str, Any], headers: dict[str, str] | None = None
):
    return await client.post("/api/v1/recommendations", json=body, headers=headers or {})


async def test_validation_amount(client: AsyncClient) -> None:
    for amount in ["0", "-5", "10000000.01", "1.001", "abc"]:
        resp = await post_raw(client, {"category": "supermarkets", "amount": amount})
        assert resp.status_code == 422, amount
        err = resp.json()["error"]
        assert err["code"] == "validation_error"
        assert err["details"][0]["field"] == "amount"
        assert err["request_id"] == resp.headers["X-Request-Id"]


async def test_amount_as_number_accepted(client: AsyncClient) -> None:
    resp = await post_raw(client, {"category": "supermarkets", "amount": 2500, "as_of": AS_OF})
    assert resp.status_code == 200
    assert resp.json()["amount"] == "2500.00"


async def test_unknown_category(client: AsyncClient) -> None:
    resp = await post_raw(client, {"category": "casino", "amount": "100"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "unknown_category"


async def test_scope_my_requires_user_id(client: AsyncClient) -> None:
    body = {"category": "supermarkets", "amount": "100", "scope": "my"}
    resp = await post_raw(client, body)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_user_id"
    resp = await post_raw(client, body, {"X-User-Id": "not-a-uuid"})
    assert resp.json()["error"]["code"] == "invalid_user_id"


async def test_unknown_route_uses_error_format(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# ---------- каталог ----------


async def test_cards_list_filters_and_pagination(client: AsyncClient) -> None:
    data = (await client.get("/api/v1/cards")).json()
    assert data["total"] < 50  # по умолчанию только актуальные
    assert all(c["terms"]["is_current"] for c in data["items"])
    everything = (await client.get("/api/v1/cards", params={"current_only": "false"})).json()
    assert everything["total"] == 50
    page = (await client.get("/api/v1/cards", params={"limit": 5, "offset": 5})).json()
    assert len(page["items"]) == 5
    assert page["items"][0] == data["items"][5]
    sever = (await client.get("/api/v1/cards", params={"bank": "bank-sever"})).json()
    assert {c["bank"]["code"] for c in sever["items"]} == {"bank-sever"}
    pharmacy = (await client.get("/api/v1/cards", params={"category": "pharmacy"})).json()
    assert all("pharmacy" in c["categories"] for c in pharmacy["items"])
    assert pharmacy["total"] >= 1


async def test_card_detail(client: AsyncClient) -> None:
    data = (await client.get("/api/v1/cards/bank-yug-semya")).json()
    assert data["is_demo_data"] is True
    assert data["terms"]["version"] == 2  # действующая, а не старая v1
    assert data["terms"]["valid_from"] == "2026-07-01"
    rules = {r["category"]["code"]: r for r in data["terms"]["rules"]}
    assert rules["supermarkets"]["rate_percent"] == "5.00"
    assert rules["supermarkets"]["monthly_limit"] == "3000.00"
    assert rules["other"]["monthly_limit"] is None


async def test_expired_card_detail_marked(client: AsyncClient) -> None:
    data = (await client.get("/api/v1/cards/bank-gorny-arhiv")).json()
    assert data["terms"]["is_current"] is False
    assert data["terms"]["valid_to"] == "2026-09-30"


async def test_card_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/cards/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "card_not_found"


# ---------- мои карты ----------


async def test_my_cards_crud(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/me/cards", headers=H)).json() == {"items": []}
    resp = await client.post("/api/v1/me/cards", json={"card_id": "bank-zapad-vsyo"}, headers=H)
    assert resp.status_code == 201
    assert resp.json()["card"]["id"] == "bank-zapad-vsyo"
    dup = await client.post("/api/v1/me/cards", json={"card_id": "bank-zapad-vsyo"}, headers=H)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "card_already_added"
    missing = await client.post("/api/v1/me/cards", json={"card_id": "nope"}, headers=H)
    assert missing.status_code == 404

    items = (await client.get("/api/v1/me/cards", headers=H)).json()["items"]
    assert [i["card"]["id"] for i in items] == ["bank-zapad-vsyo"]

    other_user = {"X-User-Id": str(uuid.uuid4())}
    assert (await client.get("/api/v1/me/cards", headers=other_user)).json() == {"items": []}

    assert (await client.delete("/api/v1/me/cards/bank-zapad-vsyo", headers=H)).status_code == 204
    assert (await client.delete("/api/v1/me/cards/bank-zapad-vsyo", headers=H)).status_code == 404
    assert (await client.get("/api/v1/me/cards", headers=H)).json() == {"items": []}


async def test_me_requires_user_id(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/me/cards")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_user_id"


async def test_no_payment_secrets_accepted(client: AsyncClient) -> None:
    body = {"card_id": "bank-zapad-vsyo", "pan": "4111111111111111", "cvv": "123"}
    resp = await client.post("/api/v1/me/cards", json=body, headers=H)
    assert resp.status_code == 422  # лишние поля запрещены схемой


async def test_scope_my_only_my_cards(client: AsyncClient) -> None:
    for card in ["bank-zapad-vsyo", "bank-yug-semya"]:
        await client.post("/api/v1/me/cards", json={"card_id": card}, headers=H)
    data = await recommend(client, scope="my")
    assert [i["card"]["id"] for i in data["eligible"]] == ["bank-yug-semya", "bank-zapad-vsyo"]


async def test_scope_my_empty(client: AsyncClient) -> None:
    data = await recommend(client, scope="my")
    assert data["eligible"] == [] and data["top"] == [] and data["not_eligible_count"] == 0


# ---------- нетривиальная фича: использованный лимит ----------


async def test_usage_changes_my_recommendation(client: AsyncClient) -> None:
    for card in ["bank-sever-produkty", "bank-yug-semya"]:
        await client.post("/api/v1/me/cards", json={"card_id": card}, headers=H)
    before = await recommend(client, scope="my")
    assert before["top"][0]["card"]["id"] == "bank-sever-produkty"

    resp = await client.put(
        "/api/v1/me/cards/bank-sever-produkty/usage",
        json={"category": "supermarkets", "used_cashback": "140.00", "period": "2026-11"},
        headers=H,
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "category": "supermarkets",
        "period": "2026-11",
        "used_cashback": "140.00",
    }

    after = await recommend(client, scope="my")
    assert after["top"][0]["card"]["id"] == "bank-yug-semya"
    produkty = next(i for i in after["eligible"] if i["card"]["id"] == "bank-sever-produkty")
    assert produkty["cashback"] == "10.00"
    assert produkty["breakdown"]["limit_used_before"] == "140.00"
    assert produkty["breakdown"]["limit_remaining_before"] == "10.00"

    # каталог использование не учитывает
    catalog = await recommend(client)
    assert catalog["top"][0]["cashback"] == "150.00"

    # другой месяц — лимит «свежий»
    december = await recommend(client, scope="my", as_of="2026-12-05")
    assert december["top"][0]["card"]["id"] == "bank-sever-produkty"


async def test_usage_put_is_idempotent_overwrite(client: AsyncClient) -> None:
    await client.post("/api/v1/me/cards", json={"card_id": "bank-yug-semya"}, headers=H)
    url = "/api/v1/me/cards/bank-yug-semya/usage"
    for used in ["100.00", "100.00", "50.00"]:
        body = {"category": "supermarkets", "used_cashback": used, "period": "2026-11"}
        await client.put(url, json=body, headers=H)
    # GET /me/cards показывает текущий месяц, поэтому проверяем через рекомендации
    data = await recommend(client, scope="my")
    assert data["top"][0]["breakdown"]["limit_used_before"] == "50.00"


async def test_usage_errors(client: AsyncClient) -> None:
    url = "/api/v1/me/cards/bank-yug-semya/usage"
    body = {"category": "supermarkets", "used_cashback": "10"}
    assert (await client.put(url, json=body, headers=H)).status_code == 404  # не моя карта
    await client.post("/api/v1/me/cards", json={"card_id": "bank-yug-semya"}, headers=H)
    bad_cat = await client.put(url, json={**body, "category": "casino"}, headers=H)
    assert bad_cat.json()["error"]["code"] == "unknown_category"
    negative = await client.put(url, json={**body, "used_cashback": "-1"}, headers=H)
    assert negative.status_code == 422
    bad_period = await client.put(url, json={**body, "period": "2026-13"}, headers=H)
    assert bad_period.status_code == 422


async def test_usage_shown_in_my_cards_for_current_month(client: AsyncClient) -> None:
    await client.post("/api/v1/me/cards", json={"card_id": "bank-yug-semya"}, headers=H)
    body = {"category": "pharmacy", "used_cashback": "12.34"}  # period = текущий месяц
    put = (await client.put("/api/v1/me/cards/bank-yug-semya/usage", json=body, headers=H)).json()
    items = (await client.get("/api/v1/me/cards", headers=H)).json()["items"]
    assert items[0]["usage"] == [put]
