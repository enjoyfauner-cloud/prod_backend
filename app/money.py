"""Единственное место, где определено округление денег в проекте.

Правило: кэшбэк округляется ВНИЗ до копейки (ROUND_DOWN, шаг 0.01), один раз,
после применения лимита. Советник не обещает больше, чем начислит банк.
"""

from decimal import ROUND_DOWN, Decimal

KOPECK = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)
ROUNDING_NAME = "down_to_kopeck"


def round_down(value: Decimal) -> Decimal:
    return value.quantize(KOPECK, rounding=ROUND_DOWN)


def percent_of(amount: Decimal, rate_percent: Decimal) -> Decimal:
    """amount × rate% без округления (округляет вызывающий через round_down)."""
    return amount * rate_percent / HUNDRED
