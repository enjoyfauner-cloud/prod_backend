"""Запросы к БД. Возвращают доменные объекты калькулятора или ORM-модели для API."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.models import (
    BASE_CATEGORY,
    Bank,
    Card,
    CardTerms,
    CardUsage,
    CashbackRule,
    Category,
    UserCard,
)
from app.services.calculator import CardOffer, Rule, Terms, Usage


def _active_at(as_of: date, terms: type[CardTerms] = CardTerms):
    """SQL-условие «версия действует на дату» (для таблицы или её псевдонима)."""
    return and_(
        terms.valid_from <= as_of,
        or_(terms.valid_to.is_(None), terms.valid_to >= as_of),
    )


def _has_active_terms(card_id: ColumnElement[int] | InstrumentedAttribute[int], as_of: date):
    """EXISTS: у карты card_id есть действующая версия. Свой псевдоним таблицы, чтобы
    подзапрос не «связался» с CardTerms внешнего запроса."""
    active = aliased(CardTerms)
    return select(active.id).where(active.card_id == card_id, _active_at(as_of, active)).exists()


def orm_terms_to_domain(t: CardTerms) -> Terms:
    return Terms(
        version=t.version,
        valid_from=t.valid_from,
        valid_to=t.valid_to,
        checked_at=t.checked_at,
        rules={
            r.category_code: Rule(r.category_code, r.rate_percent, r.monthly_limit, r.min_purchase)
            for r in t.rules
        },
    )


# ---------- категории ----------


async def list_categories(session: AsyncSession) -> list[Category]:
    return list((await session.scalars(select(Category).order_by(Category.sort_order))).all())


async def get_category(session: AsyncSession, code: str) -> Category | None:
    return await session.get(Category, code)


# ---------- данные для расчёта ----------


async def load_offers(
    session: AsyncSession, category: str, as_of: date, user_id: uuid.UUID | None = None
) -> list[CardOffer]:
    """Все карты области поиска с версиями условий (кроме будущих) и только нужными
    правилами (категория + базовая). Один SQL-запрос."""
    # Только нужные колонки, без ORM-объектов: это заметно дешевле на сотнях карт.
    stmt = (
        select(
            Card.code,
            Card.name,
            Card.promo_url,
            Bank.code,
            Bank.name,
            CardTerms.id,
            CardTerms.version,
            CardTerms.valid_from,
            CardTerms.valid_to,
            CardTerms.checked_at,
            CashbackRule.category_code,
            CashbackRule.rate_percent,
            CashbackRule.monthly_limit,
            CashbackRule.min_purchase,
        )
        .join(Bank, Bank.id == Card.bank_id)
        .outerjoin(CardTerms, and_(CardTerms.card_id == Card.id, CardTerms.valid_from <= as_of))
        .outerjoin(
            CashbackRule,
            and_(
                CashbackRule.terms_id == CardTerms.id,
                CashbackRule.category_code.in_([category, BASE_CATEGORY]),
            ),
        )
    )
    if user_id is not None:
        stmt = stmt.join(UserCard, and_(UserCard.card_id == Card.id, UserCard.user_id == user_id))

    # code -> (данные карты, {terms_id: (version, valid_from, valid_to, checked_at, rules)})
    cards: dict[str, tuple[tuple[str, str | None, str, str], dict[int, tuple[Any, ...]]]] = {}
    for row in (await session.execute(stmt)).all():
        (code, name, promo, bank_code, bank_name, terms_id, version, valid_from, valid_to,
         checked_at, rule_cat, rate, limit, min_purchase) = row  # fmt: skip
        _, versions = cards.setdefault(code, ((name, promo, bank_code, bank_name), {}))
        if terms_id is None:
            continue
        *_, rules = versions.setdefault(terms_id, (version, valid_from, valid_to, checked_at, {}))
        if rule_cat is not None:
            rules[rule_cat] = Rule(rule_cat, rate, limit, min_purchase)

    return [
        CardOffer(
            code=code,
            name=name,
            bank_code=bank_code,
            bank_name=bank_name,
            promo_url=promo,
            terms=[Terms(*t) for t in versions.values()],
        )
        for code, ((name, promo, bank_code, bank_name), versions) in cards.items()
    ]


async def load_usage(session: AsyncSession, user_id: uuid.UUID, period: str) -> Usage:
    stmt = (
        select(Card.code, CardUsage.category_code, CardUsage.used_cashback)
        .join(Card, Card.id == CardUsage.card_id)
        .where(CardUsage.user_id == user_id, CardUsage.period == period)
    )
    return {(code, cat): used for code, cat, used in (await session.execute(stmt)).all()}


# ---------- каталог ----------


def _with_terms():
    return (selectinload(Card.bank), selectinload(Card.terms).selectinload(CardTerms.rules))


async def list_cards(
    session: AsyncSession,
    as_of: date,
    category: str | None,
    bank: str | None,
    current_only: bool,
    limit: int,
    offset: int,
) -> tuple[list[Card], int]:
    conditions = []
    if bank:
        conditions.append(Bank.code == bank)
    if current_only:
        conditions.append(_has_active_terms(Card.id, as_of))
    if category:
        # Категорию ищем в той же версии, которую показываем (presenters.display_terms):
        # действующая, а если её нет — последняя истёкшая.
        has_active = _has_active_terms(CardTerms.card_id, as_of)
        later = aliased(CardTerms)
        is_latest_expired = and_(
            CardTerms.valid_to < as_of,
            ~select(later.id)
            .where(
                later.card_id == CardTerms.card_id,
                later.valid_to < as_of,
                later.valid_to > CardTerms.valid_to,
            )
            .exists(),
        )
        conditions.append(
            select(CardTerms.id)
            .join(CashbackRule, CashbackRule.terms_id == CardTerms.id)
            .where(
                CardTerms.card_id == Card.id,
                CashbackRule.category_code == category,
                or_(_active_at(as_of), and_(~has_active, is_latest_expired)),
            )
            .exists()
        )
    base = select(Card).join(Bank, Bank.id == Card.bank_id).where(*conditions)
    total = (await session.scalar(select(func.count()).select_from(base.subquery()))) or 0
    stmt = (
        base.options(*_with_terms())
        .order_by(Bank.name, Card.name, Card.code)
        .limit(limit)
        .offset(offset)
    )
    return list((await session.scalars(stmt)).all()), total


async def get_card(session: AsyncSession, code: str) -> Card | None:
    stmt = select(Card).where(Card.code == code).options(*_with_terms())
    return await session.scalar(stmt)


async def count_cards(session: AsyncSession, as_of: date) -> tuple[int, int]:
    total = await session.scalar(select(func.count(Card.id))) or 0
    current = (
        await session.scalar(
            select(func.count(func.distinct(CardTerms.card_id))).where(_active_at(as_of))
        )
        or 0
    )
    return total, current


# ---------- мои карты ----------


async def list_my_cards(session: AsyncSession, user_id: uuid.UUID) -> list[UserCard]:
    stmt = (
        select(UserCard)
        .join(Card, Card.id == UserCard.card_id)
        .where(UserCard.user_id == user_id)
        .options(
            selectinload(UserCard.card).selectinload(Card.bank),
            selectinload(UserCard.card).selectinload(Card.terms).selectinload(CardTerms.rules),
        )
        .order_by(UserCard.added_at, Card.code)
    )
    return list((await session.scalars(stmt)).all())


async def get_my_card(session: AsyncSession, user_id: uuid.UUID, card_id: int) -> UserCard | None:
    return await session.get(UserCard, (user_id, card_id))


async def add_my_card(session: AsyncSession, user_id: uuid.UUID, card_id: int) -> bool:
    """True — добавлена, False — уже была."""
    stmt = (
        insert(UserCard)
        .values(user_id=user_id, card_id=card_id)
        .on_conflict_do_nothing()
        .returning(UserCard.card_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def remove_my_card(session: AsyncSession, user_id: uuid.UUID, card_id: int) -> bool:
    await session.execute(
        delete(CardUsage).where(CardUsage.user_id == user_id, CardUsage.card_id == card_id)
    )
    result = await session.execute(
        delete(UserCard)
        .where(UserCard.user_id == user_id, UserCard.card_id == card_id)
        .returning(UserCard.card_id)
    )
    return result.scalar_one_or_none() is not None


async def usage_for_cards(
    session: AsyncSession, user_id: uuid.UUID, period: str
) -> dict[int, list[CardUsage]]:
    stmt = (
        select(CardUsage)
        .where(CardUsage.user_id == user_id, CardUsage.period == period)
        .order_by(CardUsage.category_code)
    )
    out: dict[int, list[CardUsage]] = {}
    for u in (await session.scalars(stmt)).all():
        out.setdefault(u.card_id, []).append(u)
    return out


async def set_usage(
    session: AsyncSession,
    user_id: uuid.UUID,
    card_id: int,
    category: str,
    period: str,
    used: Decimal,
) -> None:
    stmt = (
        insert(CardUsage)
        .values(
            user_id=user_id,
            card_id=card_id,
            category_code=category,
            period=period,
            used_cashback=used,
        )
        .on_conflict_do_update(
            index_elements=[
                CardUsage.user_id,
                CardUsage.card_id,
                CardUsage.category_code,
                CardUsage.period,
            ],
            set_={"used_cashback": used, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
