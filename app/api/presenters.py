"""Преобразование ORM и доменных объектов в схемы ответа."""

from datetime import date
from decimal import Decimal

from app.models import Card, CardTerms, Category
from app.schemas.api import (
    BankOut,
    Breakdown,
    CardDetail,
    CardShort,
    CardSummary,
    CategoryOut,
    RecommendationItem,
    RuleOut,
    TermsDetailOut,
    TermsOut,
)
from app.services.calculator import Scored, explain, select_terms
from app.services.repository import orm_terms_to_domain


def display_terms(card: Card, as_of: date) -> tuple[CardTerms | None, bool]:
    """Какую версию условий показывать: действующую, иначе последнюю истёкшую."""
    by_version = {t.version: t for t in card.terms}
    chosen, is_current = select_terms((orm_terms_to_domain(t) for t in card.terms), as_of)
    return (by_version[chosen.version] if chosen else None), is_current


def _terms_out(t: CardTerms, is_current: bool) -> TermsOut:
    return TermsOut(
        version=t.version,
        valid_from=t.valid_from,
        valid_to=t.valid_to,
        checked_at=t.checked_at,
        is_current=is_current,
    )


def card_short(card: Card) -> CardShort:
    return CardShort(
        id=card.code, name=card.name, bank=BankOut(code=card.bank.code, name=card.bank.name)
    )


def card_summary(card: Card, as_of: date) -> CardSummary:
    terms, is_current = display_terms(card, as_of)
    return CardSummary(
        **card_short(card).model_dump(),
        terms=_terms_out(terms, is_current) if terms else None,
        categories=[r.category_code for r in terms.rules] if terms else [],
        promo_url=card.promo_url,
    )


def card_detail(card: Card, as_of: date, categories: dict[str, Category]) -> CardDetail:
    terms, is_current = display_terms(card, as_of)
    terms_out = None
    if terms is not None:
        terms_out = TermsDetailOut(
            **_terms_out(terms, is_current).model_dump(),
            rules=[
                RuleOut(
                    category=CategoryOut(
                        code=r.category_code, name=categories[r.category_code].name
                    ),
                    rate_percent=r.rate_percent,
                    monthly_limit=r.monthly_limit,
                    min_purchase=r.min_purchase,
                )
                for r in sorted(terms.rules, key=lambda r: categories[r.category_code].sort_order)
            ],
        )
    return CardDetail(
        **card_short(card).model_dump(),
        description=card.description,
        promo_url=card.promo_url,
        is_demo_data=card.is_demo,
        terms=terms_out,
    )


def recommendation_item(s: Scored, rank: int | None, amount: Decimal) -> RecommendationItem:
    c = s.calc
    return RecommendationItem(
        rank=rank,
        card=CardShort(
            id=s.card.code,
            name=s.card.name,
            bank=BankOut(code=s.card.bank_code, name=s.card.bank_name),
        ),
        cashback=c.cashback,
        effective_rate_percent=c.effective_rate_percent,
        breakdown=Breakdown(
            rule_category=c.rule_category,
            rule_type=c.rule_type,
            fallback_reason=c.fallback_reason,
            rate_percent=c.rate_percent,
            raw_cashback=c.raw_cashback,
            monthly_limit=c.monthly_limit,
            limit_used_before=c.limit_used_before,
            limit_remaining_before=c.limit_remaining_before,
            limit_applied=c.limit_applied,
            min_purchase=c.min_purchase,
            explanation=explain(c, amount),
        ),
        terms=TermsOut(
            version=s.terms.version,
            valid_from=s.terms.valid_from,
            valid_to=s.terms.valid_to,
            checked_at=s.terms.checked_at,
            is_current=s.is_current,
        ),
        promo_url=s.card.promo_url,
    )
