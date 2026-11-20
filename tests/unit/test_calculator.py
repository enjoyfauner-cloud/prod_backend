"""Юнит-тесты формулы и ранжирования (без БД)."""

import random
from datetime import date
from decimal import Decimal

import pytest

from app.money import round_down
from app.services.calculator import (
    CardOffer,
    FallbackReason,
    Rule,
    RuleType,
    Terms,
    calculate,
    explain,
    pick_rule,
    recommend,
    select_terms,
)

D = Decimal
AS_OF = date(2026, 11, 20)


def terms(
    *rules: Rule,
    version: int = 1,
    valid_from: date = date(2026, 1, 1),
    valid_to: date | None = None,
) -> Terms:
    return Terms(version, valid_from, valid_to, date(2026, 11, 1), {r.category: r for r in rules})


def card(code: str, *t: Terms, promo: str | None = None) -> CardOffer:
    return CardOffer(code, code.title(), "bank", "Банк", promo, list(t))


# ---------- формула ----------


def test_supermarkets_2500_basic_rate() -> None:
    rule = Rule("supermarkets", D("5.00"), D("3000.00"))
    calc = calculate(rule, RuleType.CATEGORY, None, D("2500.00"))
    assert calc.cashback == D("125.00")
    assert calc.raw_cashback == D("125.00")
    assert not calc.limit_applied
    assert calc.limit_remaining_after == D("2875.00")
    assert calc.effective_rate_percent == D("5.00")


def test_limit_caps_cashback() -> None:
    rule = Rule("supermarkets", D("7.00"), D("150.00"))
    calc = calculate(rule, RuleType.CATEGORY, None, D("2500.00"))
    assert calc.raw_cashback == D("175.00")
    assert calc.cashback == D("150.00")
    assert calc.limit_applied
    assert calc.limit_remaining_after == D("0.00")


def test_used_limit_reduces_remaining() -> None:
    rule = Rule("supermarkets", D("10.00"), D("500.00"))
    calc = calculate(rule, RuleType.CATEGORY, None, D("2500.00"), used=D("450.00"))
    assert calc.limit_remaining_before == D("50.00")
    assert calc.cashback == D("50.00")
    assert calc.limit_applied


def test_used_more_than_limit_gives_zero_not_negative() -> None:
    rule = Rule("supermarkets", D("10.00"), D("500.00"))
    calc = calculate(rule, RuleType.CATEGORY, None, D("2500.00"), used=D("900.00"))
    assert calc.limit_remaining_before == D("0.00")
    assert calc.cashback == D("0.00")


def test_rounding_is_down_to_kopeck() -> None:
    # 333.33 × 1.5% = 4.99995 → 4.99 (вниз), а не 5.00
    calc = calculate(Rule("other", D("1.50")), RuleType.BASE, None, D("333.33"))
    assert calc.cashback == D("4.99")
    assert round_down(D("0.019")) == D("0.01")


def test_no_float_anywhere() -> None:
    calc = calculate(Rule("other", D("1.10")), RuleType.BASE, None, D("0.10"))
    assert isinstance(calc.cashback, Decimal)
    assert calc.cashback == D("0.00")  # 0.0011 → 0.00


# ---------- выбор правила ----------


def test_missing_category_falls_back_to_base() -> None:
    t = terms(Rule("other", D("2.00")))
    picked = pick_rule(t, "supermarkets", D("2500"))
    assert picked is not None
    rule, rule_type, reason = picked
    assert rule.category == "other"
    assert rule_type is RuleType.BASE
    assert reason is FallbackReason.NO_CATEGORY_RULE


def test_missing_category_and_no_base_is_not_eligible() -> None:
    t = terms(Rule("fuel", D("8.00")))
    assert pick_rule(t, "supermarkets", D("2500")) is None


def test_below_min_purchase_falls_back_to_base() -> None:
    t = terms(Rule("supermarkets", D("6.00"), D("1000"), D("3000")), Rule("other", D("1.50")))
    picked = pick_rule(t, "supermarkets", D("2500"))
    assert picked is not None
    assert picked[1] is RuleType.BASE
    assert picked[2] is FallbackReason.BELOW_MIN_PURCHASE
    assert calculate(picked[0], picked[1], picked[2], D("2500")).cashback == D("37.50")


def test_min_purchase_met_exactly_uses_category() -> None:
    t = terms(Rule("supermarkets", D("6.00"), None, D("3000")), Rule("other", D("1.50")))
    picked = pick_rule(t, "supermarkets", D("3000"))
    assert picked is not None and picked[1] is RuleType.CATEGORY


def test_category_other_uses_base_without_fallback_reason() -> None:
    picked = pick_rule(terms(Rule("other", D("1.00"))), "other", D("100"))
    assert picked is not None
    assert picked[1] is RuleType.BASE and picked[2] is None


# ---------- версии условий ----------


def test_select_terms_current_and_expired() -> None:
    old = terms(Rule("other", D("1")), version=1, valid_to=date(2026, 6, 30))
    new = terms(Rule("other", D("2")), version=2, valid_from=date(2026, 7, 1))
    assert select_terms([old, new], AS_OF) == (new, True)
    assert select_terms([old], AS_OF) == (old, False)
    future = terms(Rule("other", D("2")), valid_from=date(2027, 1, 1))
    assert select_terms([future], AS_OF) == (None, False)


def test_valid_to_is_inclusive() -> None:
    t = terms(Rule("other", D("1")), valid_to=AS_OF)
    assert select_terms([t], AS_OF) == (t, True)


def test_expired_cards_never_in_eligible() -> None:
    expired = card("archive", terms(Rule("supermarkets", D("10")), valid_to=date(2026, 9, 30)))
    current = card("live", terms(Rule("supermarkets", D("1"))))
    result = recommend([expired, current], "supermarkets", D("2500"), AS_OF)
    assert [s.card.code for s in result.eligible] == ["live"]
    assert [s.card.code for s in result.outdated] == ["archive"]
    assert result.outdated[0].is_current is False
    assert result.outdated[0].calc.cashback == D("250.00")  # считается, но помечено


# ---------- ранжирование ----------


def showcase() -> list[CardOffer]:
    return [
        card("produkty", terms(Rule("supermarkets", D("7"), D("150")), Rule("other", D("1")))),
        card("dvoyka", terms(Rule("supermarkets", D("5")), Rule("other", D("0.5")))),
        card("semya", terms(Rule("supermarkets", D("5"), D("3000")), Rule("other", D("1")))),
        card(
            "krupnaya",
            terms(Rule("supermarkets", D("6"), D("1000"), D("3000")), Rule("other", D("1.5"))),
        ),
        card("vsyo", terms(Rule("other", D("2")))),
        card("avto", terms(Rule("fuel", D("8")))),
        card("archive", terms(Rule("supermarkets", D("10")), valid_to=date(2026, 9, 30))),
    ]


def test_supermarkets_2500_top3() -> None:
    result = recommend(showcase(), "supermarkets", D("2500.00"), AS_OF)
    top3 = [(s.card.code, s.calc.cashback) for s in result.eligible[:3]]
    assert top3 == [("produkty", D("150.00")), ("dvoyka", D("125.00")), ("semya", D("125.00"))]
    assert [s.card.code for s in result.eligible[3:]] == ["vsyo", "krupnaya"]
    assert result.not_eligible_count == 1  # avto


def test_other_category_changes_recommendations() -> None:
    super_top = recommend(showcase(), "supermarkets", D("2500"), AS_OF).eligible[0].card.code
    fuel_top = recommend(showcase(), "fuel", D("2500"), AS_OF).eligible[0].card.code
    assert super_top != fuel_top
    assert fuel_top == "avto"


def test_tie_unlimited_beats_limited() -> None:
    result = recommend(showcase(), "supermarkets", D("2500"), AS_OF)
    assert result.eligible[1].calc.cashback == result.eligible[2].calc.cashback
    assert result.eligible[1].calc.limit_remaining_after is None


def test_tie_bigger_remaining_limit_first() -> None:
    a = card("a", terms(Rule("taxi", D("5"), D("200"))))
    b = card("b", terms(Rule("taxi", D("5"), D("900"))))
    result = recommend([a, b], "taxi", D("1000"), AS_OF)
    assert [s.card.code for s in result.eligible] == ["b", "a"]


def test_tie_longer_validity_first_then_code() -> None:
    short = card("a-short", terms(Rule("taxi", D("5")), valid_to=date(2026, 12, 31)))
    long_ = card("z-long", terms(Rule("taxi", D("5")), valid_to=date(2027, 12, 31)))
    forever = card("m-forever", terms(Rule("taxi", D("5"))))
    same1 = card("b-same", terms(Rule("taxi", D("5")), valid_to=date(2026, 12, 31)))
    result = recommend([short, long_, forever, same1], "taxi", D("1000"), AS_OF)
    assert [s.card.code for s in result.eligible] == ["m-forever", "z-long", "a-short", "b-same"]


def test_promo_does_not_affect_ranking() -> None:
    plain = card("a-plain", terms(Rule("taxi", D("5"))))
    promo = card("b-promo", terms(Rule("taxi", D("5"))), promo="https://example.com")
    result = recommend([promo, plain], "taxi", D("1000"), AS_OF)
    assert [s.card.code for s in result.eligible] == ["a-plain", "b-promo"]


def test_result_independent_of_input_order_and_repeatable() -> None:
    cards = showcase()
    expected = recommend(cards, "supermarkets", D("2500"), AS_OF)
    rng = random.Random(1)
    for _ in range(20):
        shuffled = cards[:]
        rng.shuffle(shuffled)
        assert recommend(shuffled, "supermarkets", D("2500"), AS_OF) == expected


def test_usage_lowers_card_in_ranking() -> None:
    cards = showcase()
    usage = {("produkty", "supermarkets"): D("140.00")}
    result = recommend(cards, "supermarkets", D("2500"), AS_OF, usage)
    produkty = next(s for s in result.eligible if s.card.code == "produkty")
    assert produkty.calc.cashback == D("10.00")
    assert result.eligible[0].card.code == "dvoyka"


def test_usage_applies_to_base_rule_category() -> None:
    c = card("vsyo", terms(Rule("other", D("2"), D("100"))))
    usage = {("vsyo", "other"): D("90")}
    result = recommend([c], "supermarkets", D("2500"), AS_OF, usage)
    assert result.eligible[0].calc.cashback == D("10.00")


@pytest.mark.parametrize(
    ("calc_args", "fragment"),
    [
        ((Rule("s", D("7"), D("150")), RuleType.CATEGORY, None), "упёрлись в лимит"),
        ((Rule("s", D("5")), RuleType.CATEGORY, None), "лимита нет"),
        ((Rule("other", D("2")), RuleType.BASE, FallbackReason.NO_CATEGORY_RULE), "нет условия"),
    ],
)
def test_explain(calc_args: tuple[Rule, RuleType, FallbackReason | None], fragment: str) -> None:
    assert fragment in explain(calculate(*calc_args, amount=D("2500")), D("2500"))
