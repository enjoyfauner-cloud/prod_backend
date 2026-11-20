"""Расчёт рекомендаций."""

import time

from fastapi import APIRouter

from app.api.deps import SessionDep, SettingsDep, UserIdHeader, month_of, parse_user_id, today
from app.api.presenters import recommendation_item
from app.errors import UnknownCategoryError
from app.money import ROUNDING_NAME
from app.observability.metrics import RECOMMENDATION_CALC, RECOMMENDATIONS
from app.schemas.api import (
    CategoryOut,
    ErrorResponse,
    RecommendationRequest,
    RecommendationResponse,
)
from app.services import repository as repo
from app.services.calculator import recommend

router = APIRouter(prefix="/api/v1", tags=["recommendations"])


@router.post(
    "/recommendations",
    summary="Топ выгодных карт для покупки + полный список с разбором расчёта",
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def recommendations(
    body: RecommendationRequest,
    session: SessionDep,
    settings: SettingsDep,
    x_user_id: UserIdHeader = None,
) -> RecommendationResponse:
    category = await repo.get_category(session, body.category)
    if category is None:
        raise UnknownCategoryError(f"Неизвестная категория: {body.category}")
    as_of = body.as_of or today(settings)

    user_id = parse_user_id(x_user_id) if body.scope == "my" else None
    offers = await repo.load_offers(session, category.code, as_of, user_id)
    usage = await repo.load_usage(session, user_id, month_of(as_of)) if user_id else {}

    started = time.perf_counter()
    result = recommend(offers, category.code, body.amount, as_of, usage)
    RECOMMENDATION_CALC.labels(body.scope).observe(time.perf_counter() - started)
    RECOMMENDATIONS.labels(body.scope, category.code).inc()

    eligible = [recommendation_item(s, i, body.amount) for i, s in enumerate(result.eligible, 1)]
    return RecommendationResponse(
        category=CategoryOut(code=category.code, name=category.name),
        amount=body.amount,
        scope=body.scope,
        as_of=as_of,
        rounding=ROUNDING_NAME,
        top=eligible[: body.top],
        eligible=eligible,
        outdated=[recommendation_item(s, None, body.amount) for s in result.outdated],
        not_eligible_count=result.not_eligible_count,
    )
