"""Pydantic-схемы HTTP API. Деньги и проценты в ответах сериализуются строкой «0.00»."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

from app.services.calculator import FallbackReason, RuleType

MAX_AMOUNT = Decimal("10000000")


def _fmt(value: Decimal) -> str:
    return f"{value:.2f}"


MoneyOut = Annotated[Decimal, PlainSerializer(_fmt, return_type=str, when_used="json")]
AmountIn = Annotated[Decimal, Field(gt=0, le=MAX_AMOUNT, max_digits=10, decimal_places=2)]
UsedIn = Annotated[Decimal, Field(ge=0, le=MAX_AMOUNT, max_digits=10, decimal_places=2)]
Period = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------- справочники ----------


class CategoryOut(BaseModel):
    code: str
    name: str


class BankOut(BaseModel):
    code: str
    name: str


class TermsOut(BaseModel):
    version: int
    valid_from: date
    valid_to: date | None
    checked_at: date
    is_current: bool


class RuleOut(BaseModel):
    category: CategoryOut
    rate_percent: MoneyOut
    monthly_limit: MoneyOut | None
    min_purchase: MoneyOut | None


class TermsDetailOut(TermsOut):
    rules: list[RuleOut]


class CardShort(BaseModel):
    id: str
    name: str
    bank: BankOut


class CardSummary(CardShort):
    terms: TermsOut | None
    categories: list[str]
    promo_url: str | None


class CardDetail(CardShort):
    description: str
    promo_url: str | None
    is_demo_data: bool
    terms: TermsDetailOut | None


class CardPage(BaseModel):
    items: list[CardSummary]
    total: int
    limit: int
    offset: int


# ---------- рекомендации ----------


class RecommendationRequest(_In):
    category: str = Field(examples=["supermarkets"])
    amount: AmountIn = Field(examples=["2500.00"])
    scope: Literal["catalog", "my"] = "catalog"
    top: Annotated[int, Field(ge=1, le=20)] = 3
    as_of: date | None = Field(default=None, description="Дата расчёта; по умолчанию сегодня")


class Breakdown(BaseModel):
    rule_category: str
    rule_type: RuleType
    fallback_reason: FallbackReason | None
    rate_percent: MoneyOut
    raw_cashback: MoneyOut
    monthly_limit: MoneyOut | None
    limit_used_before: MoneyOut
    limit_remaining_before: MoneyOut | None
    limit_applied: bool
    min_purchase: MoneyOut | None
    explanation: str


class RecommendationItem(BaseModel):
    rank: int | None
    card: CardShort
    cashback: MoneyOut
    effective_rate_percent: MoneyOut
    breakdown: Breakdown
    terms: TermsOut
    promo_url: str | None


class RecommendationResponse(BaseModel):
    category: CategoryOut
    amount: MoneyOut
    scope: Literal["catalog", "my"]
    as_of: date
    rounding: str
    top: list[RecommendationItem]
    eligible: list[RecommendationItem]
    outdated: list[RecommendationItem]
    not_eligible_count: int


# ---------- мои карты ----------


class AddMyCard(_In):
    card_id: str


class UsageIn(_In):
    category: str
    used_cashback: UsedIn
    period: Period | None = None


class UsageOut(BaseModel):
    category: str
    period: str
    used_cashback: MoneyOut


class MyCardOut(BaseModel):
    card: CardSummary
    added_at: datetime
    usage: list[UsageOut]


class MyCardsOut(BaseModel):
    items: list[MyCardOut]


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail]
    request_id: str | None


class ErrorResponse(BaseModel):
    error: ErrorBody
