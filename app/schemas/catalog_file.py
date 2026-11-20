"""Формат файла справочника (JSON; CSV приводится к этой же модели).

Всё, что можно проверить без БД, проверяется здесь: импорт либо целиком
валиден, либо не применяется вовсе.
"""

from datetime import date
from decimal import Decimal
from itertools import pairwise
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Code = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=128)]
Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleIn(_Strict):
    category: str
    rate_percent: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)]
    monthly_limit: Money | None = None
    min_purchase: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None


class TermsIn(_Strict):
    version: Annotated[int, Field(ge=1)]
    valid_from: date
    valid_to: date | None = None
    checked_at: date
    source: str = ""
    rules: Annotated[list[RuleIn], Field(min_length=1)]

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError(f"v{self.version}: valid_to раньше valid_from")
        categories = [r.category for r in self.rules]
        if len(categories) != len(set(categories)):
            raise ValueError(f"v{self.version}: категория указана дважды")
        return self


class CardIn(_Strict):
    code: Code
    bank: Code
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    description: str = ""
    promo_url: str | None = None
    terms: Annotated[list[TermsIn], Field(min_length=1)]

    @model_validator(mode="after")
    def _check_versions(self) -> Self:
        versions = [t.version for t in self.terms]
        if len(versions) != len(set(versions)):
            raise ValueError(f"{self.code}: версии условий повторяются")
        # Периоды не должны пересекаться: на любую дату действует не больше одной версии.
        ordered = sorted(self.terms, key=lambda t: t.valid_from)
        for prev, nxt in pairwise(ordered):
            if prev.valid_to is None or prev.valid_to >= nxt.valid_from:
                raise ValueError(
                    f"{self.code}: периоды версий v{prev.version} и v{nxt.version} пересекаются"
                )
        return self


class BankIn(_Strict):
    code: Code
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class CatalogMeta(_Strict):
    demo: bool = True
    note: str = ""


class CatalogFile(_Strict):
    meta: CatalogMeta = CatalogMeta()
    banks: list[BankIn]
    cards: list[CardIn]

    @model_validator(mode="after")
    def _check_refs(self) -> Self:
        bank_codes = {b.code for b in self.banks}
        if len(bank_codes) != len(self.banks):
            raise ValueError("коды банков повторяются")
        card_codes: set[str] = set()
        names: set[tuple[str, str]] = set()
        for card in self.cards:
            if card.bank not in bank_codes:
                raise ValueError(f"{card.code}: неизвестный банк {card.bank}")
            if card.code in card_codes:
                raise ValueError(f"код карты {card.code} повторяется")
            if (card.bank, card.name) in names:
                raise ValueError(f"{card.code}: у банка уже есть карта «{card.name}»")
            card_codes.add(card.code)
            names.add((card.bank, card.name))
        return self
