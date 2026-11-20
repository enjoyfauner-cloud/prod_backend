"""Категории и каталог карт."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import SessionDep, SettingsDep, today
from app.api.presenters import card_detail, card_summary
from app.errors import CardNotFoundError, UnknownCategoryError
from app.schemas.api import CardDetail, CardPage, CategoryOut, ErrorResponse
from app.services import repository as repo

router = APIRouter(prefix="/api/v1", tags=["catalog"])


@router.get("/categories", summary="Категории покупок")
async def categories(session: SessionDep) -> list[CategoryOut]:
    return [CategoryOut(code=c.code, name=c.name) for c in await repo.list_categories(session)]


@router.get(
    "/cards",
    summary="Каталог карт с фильтрами",
    responses={422: {"model": ErrorResponse}},
)
async def list_cards(
    session: SessionDep,
    settings: SettingsDep,
    category: Annotated[str | None, Query(description="Есть правило для категории")] = None,
    bank: Annotated[str | None, Query(description="Код банка")] = None,
    current_only: Annotated[bool, Query(description="Только с действующими условиями")] = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CardPage:
    if category is not None and await repo.get_category(session, category) is None:
        raise UnknownCategoryError(f"Неизвестная категория: {category}")
    as_of = today(settings)
    cards, total = await repo.list_cards(
        session, as_of, category, bank, current_only, limit, offset
    )
    return CardPage(
        items=[card_summary(c, as_of) for c in cards], total=total, limit=limit, offset=offset
    )


@router.get(
    "/cards/{card_id}",
    summary="Подробные условия карты",
    responses={404: {"model": ErrorResponse}},
)
async def get_card(card_id: str, session: SessionDep, settings: SettingsDep) -> CardDetail:
    card = await repo.get_card(session, card_id)
    if card is None:
        raise CardNotFoundError(f"Карта {card_id} не найдена")
    categories = {c.code: c for c in await repo.list_categories(session)}
    return card_detail(card, today(settings), categories)
