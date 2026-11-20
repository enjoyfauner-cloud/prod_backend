"""Чистый расчёт кэшбэка и ранжирование. Без БД, без текущего времени, без I/O.

Одинаковый вход всегда даёт одинаковый выход, поэтому формула полностью
покрывается юнит-тестами, а результат воспроизводим и не зависит от порядка карт.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.models import BASE_CATEGORY
from app.money import ZERO, percent_of, round_down

# ---------- входные данные ----------


@dataclass(frozen=True, slots=True)
class Rule:
    category: str
    rate_percent: Decimal
    monthly_limit: Decimal | None = None  # None = без лимита
    min_purchase: Decimal | None = None  # None = без минимальной суммы


@dataclass(frozen=True, slots=True)
class Terms:
    version: int
    valid_from: date
    valid_to: date | None  # None = бессрочно
    checked_at: date
    rules: Mapping[str, Rule]  # category -> Rule

    def is_active(self, as_of: date) -> bool:
        return self.valid_from <= as_of and (self.valid_to is None or as_of <= self.valid_to)

    def is_expired(self, as_of: date) -> bool:
        return self.valid_to is not None and self.valid_to < as_of


@dataclass(frozen=True, slots=True)
class CardOffer:
    code: str
    name: str
    bank_code: str
    bank_name: str
    promo_url: str | None
    terms: Sequence[Terms]


# ---------- результат ----------


class RuleType(StrEnum):
    CATEGORY = "category"
    BASE = "base"


class FallbackReason(StrEnum):
    NO_CATEGORY_RULE = "no_category_rule"
    BELOW_MIN_PURCHASE = "below_min_purchase"


@dataclass(frozen=True, slots=True)
class Calculation:
    rule_category: str
    rule_type: RuleType
    fallback_reason: FallbackReason | None
    rate_percent: Decimal
    raw_cashback: Decimal
    monthly_limit: Decimal | None
    min_purchase: Decimal | None
    limit_used_before: Decimal
    limit_remaining_before: Decimal | None
    limit_remaining_after: Decimal | None
    limit_applied: bool
    cashback: Decimal
    effective_rate_percent: Decimal


@dataclass(frozen=True, slots=True)
class Scored:
    card: CardOffer
    terms: Terms
    is_current: bool
    calc: Calculation


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    eligible: list[Scored]  # уже отсортированы
    outdated: list[Scored]  # отсортированы тем же правилом, в топ не попадают
    not_eligible_count: int


# Использованный кэшбэк: (card_code, category) -> сумма за месяц
Usage = Mapping[tuple[str, str], Decimal]


# ---------- шаги расчёта ----------


def select_terms(terms: Iterable[Terms], as_of: date) -> tuple[Terms | None, bool]:
    """Версия условий на дату: (terms, is_current).

    Действующая → (она, True). Иначе последняя истёкшая → (она, False).
    Только будущие или ничего → (None, False).
    """
    all_terms = list(terms)
    active = [t for t in all_terms if t.is_active(as_of)]
    if active:
        return max(active, key=lambda t: t.version), True
    expired = [t for t in all_terms if t.is_expired(as_of)]
    if expired:
        return max(expired, key=lambda t: (t.valid_to or date.min, t.version)), False
    return None, False


def pick_rule(
    terms: Terms, category: str, amount: Decimal
) -> tuple[Rule, RuleType, FallbackReason | None] | None:
    """Правило категории → иначе базовое ('other') → иначе карта не подходит."""
    fallback: FallbackReason | None = None
    rule = terms.rules.get(category)
    if rule is not None and category != BASE_CATEGORY:
        if rule.min_purchase is None or amount >= rule.min_purchase:
            return rule, RuleType.CATEGORY, None
        fallback = FallbackReason.BELOW_MIN_PURCHASE
    elif category != BASE_CATEGORY:
        fallback = FallbackReason.NO_CATEGORY_RULE

    base = terms.rules.get(BASE_CATEGORY)
    if base is None or (base.min_purchase is not None and amount < base.min_purchase):
        return None
    return base, RuleType.BASE, fallback


def calculate(
    rule: Rule,
    rule_type: RuleType,
    fallback: FallbackReason | None,
    amount: Decimal,
    used: Decimal = ZERO,
) -> Calculation:
    """возврат = min(сумма × ставка, остаток лимита), округление вниз до копейки."""
    raw = percent_of(amount, rule.rate_percent)
    remaining_before: Decimal | None = None
    limit_applied = False
    capped = raw
    if rule.monthly_limit is not None:
        remaining_before = max(rule.monthly_limit - used, ZERO)
        if raw > remaining_before:
            capped = remaining_before
            limit_applied = True
    cashback = round_down(capped)
    remaining_after = None if remaining_before is None else remaining_before - cashback
    return Calculation(
        rule_category=rule.category,
        rule_type=rule_type,
        fallback_reason=fallback,
        rate_percent=rule.rate_percent,
        raw_cashback=round_down(raw),
        monthly_limit=rule.monthly_limit,
        min_purchase=rule.min_purchase,
        limit_used_before=round_down(used),
        limit_remaining_before=remaining_before,
        limit_remaining_after=remaining_after,
        limit_applied=limit_applied,
        cashback=cashback,
        effective_rate_percent=round_down(cashback * 100 / amount),
    )


def ranking_key(s: Scored) -> tuple[Decimal, int, Decimal, int, int, str]:
    """Детерминированный порядок (по возрастанию ключа = лучше раньше):

    1. кэшбэк — больше лучше;
    2. остаток лимита после покупки — без лимита лучше всех, далее больше лучше;
    3. срок действия условий — бессрочные лучше, далее позже valid_to лучше;
    4. код карты по алфавиту — стабильный ключ, не зависящий от порядка в БД.

    promo_url в ключе отсутствует намеренно: реклама не влияет на ранжирование.
    """
    c = s.calc
    unlimited = c.limit_remaining_after is None
    valid_to = s.terms.valid_to
    return (
        -c.cashback,
        0 if unlimited else 1,
        -(c.limit_remaining_after or ZERO),
        0 if valid_to is None else 1,
        -(valid_to.toordinal() if valid_to else 0),
        s.card.code,
    )


def recommend(
    cards: Iterable[CardOffer],
    category: str,
    amount: Decimal,
    as_of: date,
    usage: Usage | None = None,
) -> RecommendationResult:
    usage = usage or {}
    eligible: list[Scored] = []
    outdated: list[Scored] = []
    total = 0
    for card in cards:
        total += 1
        terms, is_current = select_terms(card.terms, as_of)
        if terms is None:
            continue
        picked = pick_rule(terms, category, amount)
        if picked is None:
            continue
        rule, rule_type, fallback = picked
        used = usage.get((card.code, rule.category), ZERO)
        scored = Scored(card, terms, is_current, calculate(rule, rule_type, fallback, amount, used))
        (eligible if is_current else outdated).append(scored)
    eligible.sort(key=ranking_key)
    outdated.sort(key=ranking_key)
    return RecommendationResult(
        eligible=eligible,
        outdated=outdated,
        not_eligible_count=total - len(eligible) - len(outdated),
    )


def explain(calc: Calculation, amount: Decimal) -> str:
    """Человекочитаемое «как посчитано»."""
    parts: list[str] = []
    if calc.fallback_reason is FallbackReason.NO_CATEGORY_RULE:
        parts.append("для категории нет условия, применена базовая ставка")
    elif calc.fallback_reason is FallbackReason.BELOW_MIN_PURCHASE:
        parts.append("сумма меньше минимальной для категории, применена базовая ставка")
    parts.append(f"{amount:.2f} × {calc.rate_percent:.2f}% = {calc.raw_cashback:.2f} ₽")
    if calc.monthly_limit is None:
        parts.append("лимита нет")
    elif calc.limit_applied:
        parts.append(
            f"упёрлись в лимит: осталось {calc.limit_remaining_before:.2f} ₽ "
            f"из {calc.monthly_limit:.2f} ₽ → {calc.cashback:.2f} ₽"
        )
    else:
        parts.append(f"лимит {calc.monthly_limit:.2f} ₽ не достигнут")
    return "; ".join(parts)
