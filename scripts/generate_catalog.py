"""Генератор ДЕМОНСТРАЦИОННОГО каталога карт. Все банки и условия вымышлены.

Детерминирован: одинаковые --seed и --count дают байт-в-байт одинаковый файл.

    python -m scripts.generate_catalog --count 42 --out data/demo_catalog.json   # демо (50 карт)
    python -m scripts.generate_catalog --count 600 --out data/generated_600.json  # нагрузка

В каталог всегда входят 8 «витринных» карт с заранее известными свойствами
(лимит, тай-брейк, мин. сумма, нет категории, истёкшие условия, промо). На них
построен сценарий «Супермаркеты, 2 500 ₽» и тесты.
"""

import argparse
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.schemas.catalog_file import CatalogFile
from app.services.catalog_import import dump_catalog

BANKS = [
    ("bank-sever", "Банк Север"),
    ("bank-yug", "Юг-Банк"),
    ("bank-vostok", "Восток Финанс"),
    ("bank-zapad", "Запад Кредит"),
    ("bank-reka", "Река Банк"),
    ("bank-gorny", "Горный Банк"),
]
CATEGORIES = ["supermarkets", "restaurants", "fuel", "taxi", "pharmacy", "online"]
PROMO = "https://example.com/apply"

CURRENT = {"valid_from": "2026-07-01", "valid_to": "2027-06-30", "checked_at": "2026-11-15"}
OPEN_ENDED = {"valid_from": "2026-07-01", "valid_to": None, "checked_at": "2026-11-15"}
OLD = {"valid_from": "2026-01-01", "valid_to": "2026-06-30", "checked_at": "2026-06-20"}
EXPIRED = {"valid_from": "2026-01-01", "valid_to": "2026-09-30", "checked_at": "2026-09-01"}
SOURCE = "Демонстрационные данные, сгенерированы для олимпиады"


def _rule(
    category: str, rate: str, limit: str | None = None, min_purchase: str | None = None
) -> dict[str, Any]:
    return {
        "category": category,
        "rate_percent": rate,
        "monthly_limit": limit,
        "min_purchase": min_purchase,
    }


def _terms(version: int, period: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any]:
    return {"version": version, **period, "source": SOURCE, "rules": rules}


def _card(
    code: str, bank: str, name: str, desc: str, terms: list[dict[str, Any]], promo: bool = False
) -> dict[str, Any]:
    return {
        "code": code,
        "bank": bank,
        "name": name,
        "description": desc,
        "promo_url": PROMO if promo else None,
        "terms": terms,
    }


def showcase_cards() -> list[dict[str, Any]]:
    """Витрина для сценария «Супермаркеты, 2 500 ₽» (ожидаемые результаты в tests/)."""
    return [
        # 2500 × 7% = 175, но лимит 150 → 150 (сработал лимит). Ожидается №1.
        _card(
            "bank-sever-produkty",
            "bank-sever",
            "Продуктовая",
            "7% в супермаркетах, лимит 150 ₽",
            [_terms(1, CURRENT, [_rule("supermarkets", "7.00", "150.00"), _rule("other", "1.00")])],
        ),
        # 5% без лимита → 125. Ничья с bank-yug-semya, но без лимита → выше. Ожидается №2.
        _card(
            "bank-reka-dvoyka",
            "bank-reka",
            "Двойка",
            "5% в супермаркетах без лимита",
            [_terms(1, OPEN_ENDED, [_rule("supermarkets", "5.00"), _rule("other", "0.50")])],
        ),
        # 5% с лимитом 3000 → 125. Ожидается №3.
        _card(
            "bank-yug-semya",
            "bank-yug",
            "Семейная",
            "5% в супермаркетах и аптеках",
            [
                _terms(1, OLD, [_rule("supermarkets", "3.00", "1000.00"), _rule("other", "1.00")]),
                _terms(
                    2,
                    CURRENT,
                    [
                        _rule("supermarkets", "5.00", "3000.00"),
                        _rule("pharmacy", "5.00", "3000.00"),
                        _rule("other", "1.00"),
                    ],
                ),
            ],
            promo=True,
        ),
        # Мин. сумма 3000 не выполнена → базовая 1.5% → 37.50.
        _card(
            "bank-vostok-krupnaya",
            "bank-vostok",
            "Крупная покупка",
            "6% в супермаркетах от 3000 ₽",
            [
                _terms(
                    1,
                    CURRENT,
                    [_rule("supermarkets", "6.00", "1000.00", "3000.00"), _rule("other", "1.50")],
                )
            ],
        ),
        # Нет правила для супермаркетов → базовая 2% → 50.
        _card(
            "bank-zapad-vsyo",
            "bank-zapad",
            "Всё и сразу",
            "2% на всё без лимита",
            [_terms(1, OPEN_ENDED, [_rule("other", "2.00")])],
        ),
        # Истёкшие условия: 10% → было бы 250, но в топ не попадает (outdated).
        _card(
            "bank-gorny-arhiv",
            "bank-gorny",
            "Архивная",
            "Условия истекли 30.09.2026",
            [
                _terms(
                    1, EXPIRED, [_rule("supermarkets", "10.00", "500.00"), _rule("other", "1.00")]
                )
            ],
        ),
        # Промо-карта с низкой ставкой: промо не поднимает её в рейтинге.
        _card(
            "bank-sever-reklama",
            "bank-sever",
            "Рекламная",
            "Партнёрская карта, 1% везде",
            [_terms(1, CURRENT, [_rule("other", "1.00")])],
            promo=True,
        ),
        # Только АЗС и такси, без базовой ставки → для супермаркетов не подходит.
        _card(
            "bank-vostok-avto",
            "bank-vostok",
            "Авто",
            "Только АЗС и такси",
            [
                _terms(
                    1, CURRENT, [_rule("fuel", "8.00", "1000.00"), _rule("taxi", "5.00", "500.00")]
                )
            ],
        ),
    ]


ADJECTIVES = [
    "Умная",
    "Яркая",
    "Городская",
    "Цифровая",
    "Простая",
    "Щедрая",
    "Вечерняя",
    "Летняя",
    "Быстрая",
    "Тёплая",
    "Тихая",
    "Звёздная",
]
NOUNS = ["Плюс", "Лайт", "Про", "Макс", "Классика", "Онлайн", "Мини", "Престиж"]
RATES = ["1.00", "2.00", "3.00", "4.00", "5.00", "7.00", "10.00"]
SUPERMARKET_RATES = ["1.00", "2.00", "3.00", "4.00"]  # витрина остаётся в топе
LIMITS = [None, "300.00", "500.00", "1000.00", "2000.00", "5000.00"]
BASE_RATES = [None, "0.50", "1.00", "1.50"]


def random_cards(count: int, rng: random.Random) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for i in range(count):
        bank_code, _ = BANKS[i % len(BANKS)]
        name = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)} {i + 1}"
        rules: list[dict[str, Any]] = []
        for category in rng.sample(CATEGORIES, rng.randint(1, 3)):
            rates = SUPERMARKET_RATES if category == "supermarkets" else RATES
            min_purchase = rng.choice([None, None, None, "500.00", "1000.00"])
            rules.append(_rule(category, rng.choice(rates), rng.choice(LIMITS), min_purchase))
        base = rng.choice(BASE_RATES)
        if base is not None:
            rules.append(_rule("other", base))
        rules.sort(key=lambda r: r["category"])

        kind = rng.random()
        if kind < 0.15:  # только истёкшие условия
            terms = [_terms(1, EXPIRED, rules)]
        elif kind < 0.35:  # старая версия + текущая
            old_rules = [{**r, "rate_percent": str(Decimal(r["rate_percent"]) / 2)} for r in rules]
            for r in old_rules:
                r["rate_percent"] = str(Decimal(r["rate_percent"]).quantize(Decimal("0.01")))
            terms = [_terms(1, OLD, old_rules), _terms(2, CURRENT, rules)]
        else:
            terms = [_terms(1, rng.choice([CURRENT, OPEN_ENDED]), rules)]
        cards.append(
            _card(
                f"{bank_code}-card-{i + 1:04d}",
                bank_code,
                name,
                "Сгенерированная демонстрационная карта",
                terms,
                promo=rng.random() < 0.3,
            )
        )
    return cards


def build(count: int, seed: int) -> CatalogFile:
    rng = random.Random(seed)
    data = {
        "meta": {"demo": True, "note": "ДЕМОНСТРАЦИОННЫЕ данные: банки и условия вымышлены"},
        "banks": [{"code": c, "name": n} for c, n in BANKS],
        "cards": showcase_cards() + random_cards(count, rng),
    }
    return CatalogFile.model_validate(data)  # тот же валидатор, что и при импорте


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=42, help="число случайных карт (+8 витринных)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/demo_catalog.json"))
    args = parser.parse_args()
    catalog = build(args.count, args.seed)
    dump_catalog(catalog, args.out)
    print(f"{args.out}: {len(catalog.cards)} карт")


if __name__ == "__main__":
    main()
