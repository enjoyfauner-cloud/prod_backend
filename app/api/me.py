"""«Мои карты» и учёт использованного лимита. Идентификация — анонимный UUID в X-User-Id."""

from datetime import date

from fastapi import APIRouter, Response, status

from app.api.deps import SessionDep, SettingsDep, UserIdDep, month_of, today
from app.api.presenters import card_summary
from app.errors import CardAlreadyAddedError, CardNotFoundError, UnknownCategoryError
from app.models import Card, CardUsage, UserCard
from app.schemas.api import AddMyCard, ErrorResponse, MyCardOut, MyCardsOut, UsageIn, UsageOut
from app.services import repository as repo

router = APIRouter(
    prefix="/api/v1/me",
    tags=["my cards"],
    responses={400: {"model": ErrorResponse, "description": "Нет или неверный X-User-Id"}},
)


def _usage_out(rows: list[CardUsage]) -> list[UsageOut]:
    return [
        UsageOut(category=u.category_code, period=u.period, used_cashback=u.used_cashback)
        for u in rows
    ]


def _my_card_out(uc: UserCard, usage: list[CardUsage], as_of: date) -> MyCardOut:
    return MyCardOut(
        card=card_summary(uc.card, as_of),
        added_at=uc.added_at,
        usage=_usage_out(usage),
    )


async def _catalog_card(session: SessionDep, card_id: str) -> Card:
    card = await repo.get_card(session, card_id)
    if card is None:
        raise CardNotFoundError(f"Карта {card_id} не найдена в каталоге")
    return card


async def _owned_card(session: SessionDep, user_id: UserIdDep, card_id: str) -> Card:
    card = await repo.get_card(session, card_id)
    if card is None or await repo.get_my_card(session, user_id, card.id) is None:
        raise CardNotFoundError(f"Карты {card_id} нет в «моих картах»")
    return card


@router.get("/cards", summary="Мои карты с использованием лимитов за текущий месяц")
async def list_my_cards(
    session: SessionDep, settings: SettingsDep, user_id: UserIdDep
) -> MyCardsOut:
    as_of = today(settings)
    cards = await repo.list_my_cards(session, user_id)
    usage = await repo.usage_for_cards(session, user_id, month_of(as_of))
    return MyCardsOut(items=[_my_card_out(uc, usage.get(uc.card_id, []), as_of) for uc in cards])


@router.post(
    "/cards",
    status_code=status.HTTP_201_CREATED,
    summary="Добавить карту из каталога",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def add_my_card(
    body: AddMyCard, session: SessionDep, settings: SettingsDep, user_id: UserIdDep
) -> MyCardOut:
    card = await _catalog_card(session, body.card_id)
    if not await repo.add_my_card(session, user_id, card.id):
        raise CardAlreadyAddedError(f"Карта {body.card_id} уже добавлена")
    await session.commit()
    uc = await repo.get_my_card(session, user_id, card.id)
    assert uc is not None
    uc.card = card
    return _my_card_out(uc, [], today(settings))


@router.delete(
    "/cards/{card_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Убрать карту (вместе с данными об использовании)",
    responses={404: {"model": ErrorResponse}},
)
async def remove_my_card(card_id: str, session: SessionDep, user_id: UserIdDep) -> Response:
    card = await _owned_card(session, user_id, card_id)
    await repo.remove_my_card(session, user_id, card.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/cards/{card_id}/usage",
    summary="Сколько кэшбэка по категории уже получено за месяц (перезаписывает значение)",
    responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def put_usage(
    card_id: str, body: UsageIn, session: SessionDep, settings: SettingsDep, user_id: UserIdDep
) -> UsageOut:
    card = await _owned_card(session, user_id, card_id)
    if await repo.get_category(session, body.category) is None:
        raise UnknownCategoryError(f"Неизвестная категория: {body.category}")
    period = body.period or month_of(today(settings))
    await repo.set_usage(session, user_id, card.id, body.category, period, body.used_cashback)
    await session.commit()
    return UsageOut(category=body.category, period=period, used_cashback=body.used_cashback)
