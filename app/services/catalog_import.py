"""Идемпотентный импорт справочника.

Ключи:
- банк: code;
- карта: code (публичный id; пара (банк, название) тоже уникальна);
- версия условий: (карта, version).

Повторный импорт того же файла ничего не дублирует: строки обновляются (upsert).
Для каждой карты из файла её версии и правила приводятся точно к содержимому файла
(лишние удаляются). Карты, которых нет в файле, не трогаются. Всё в одной транзакции.
"""

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bank, Card, CardTerms, CashbackRule, Category
from app.schemas.catalog_file import CatalogFile

logger = logging.getLogger("app.import")


class CatalogImportError(Exception):
    pass


@dataclass(frozen=True)
class ImportReport:
    banks: int
    cards: int
    terms: int
    rules: int


def load_catalog_file(path: Path) -> CatalogFile:
    if path.suffix.lower() == ".csv":
        return CatalogFile.model_validate(_csv_to_dict(path))
    return CatalogFile.model_validate_json(path.read_text(encoding="utf-8"))


def _csv_to_dict(path: Path) -> dict[str, Any]:
    """CSV: одна строка = одно правило. Колонки:
    bank_code,bank_name,card_code,card_name,description,promo_url,
    version,valid_from,valid_to,checked_at,category,rate_percent,monthly_limit,min_purchase
    """
    banks: dict[str, dict[str, Any]] = {}
    cards: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            banks[row["bank_code"]] = {"code": row["bank_code"], "name": row["bank_name"]}
            card = cards.setdefault(
                row["card_code"],
                {
                    "code": row["card_code"],
                    "bank": row["bank_code"],
                    "name": row["card_name"],
                    "description": row.get("description") or "",
                    "promo_url": row.get("promo_url") or None,
                    "terms": {},
                },
            )
            terms = card["terms"].setdefault(
                row["version"],
                {
                    "version": int(row["version"]),
                    "valid_from": row["valid_from"],
                    "valid_to": row.get("valid_to") or None,
                    "checked_at": row["checked_at"],
                    "rules": [],
                },
            )
            terms["rules"].append(
                {
                    "category": row["category"],
                    "rate_percent": row["rate_percent"],
                    "monthly_limit": row.get("monthly_limit") or None,
                    "min_purchase": row.get("min_purchase") or None,
                }
            )
    for card in cards.values():
        card["terms"] = list(card["terms"].values())
    return {"meta": {"demo": True}, "banks": list(banks.values()), "cards": list(cards.values())}


async def import_catalog(session: AsyncSession, catalog: CatalogFile) -> ImportReport:
    known_categories = set((await session.scalars(select(Category.code))).all())
    for card in catalog.cards:
        for terms in card.terms:
            for rule in terms.rules:
                if rule.category not in known_categories:
                    raise CatalogImportError(
                        f"{card.code} v{terms.version}: неизвестная категория {rule.category}"
                    )

    bank_ids: dict[str, int] = {}
    for bank in catalog.banks:
        stmt = (
            insert(Bank)
            .values(code=bank.code, name=bank.name)
            .on_conflict_do_update(index_elements=[Bank.code], set_={"name": bank.name})
            .returning(Bank.id)
        )
        bank_ids[bank.code] = (await session.execute(stmt)).scalar_one()

    terms_count = rules_count = 0
    for card in catalog.cards:
        values = {
            "code": card.code,
            "bank_id": bank_ids[card.bank],
            "name": card.name,
            "description": card.description,
            "promo_url": card.promo_url,
            "is_demo": catalog.meta.demo,
        }
        # Идентичность карты = code (публичный id, на него ссылаются «мои карты»).
        # Переименование или перенос в другой банк обновляет ту же строку.
        stmt = (
            insert(Card)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[Card.code],
                set_={k: v for k, v in values.items() if k != "code"},
            )
            .returning(Card.id)
        )
        # Уникальность (банк, название) проверяется отложенно, в конце импорта (миграция 0002):
        # так файл может, например, поменять названия двух карт местами.
        card_id = (await session.execute(stmt)).scalar_one()

        # Файл — источник правды для условий карты: версии, которых в нём нет, удаляем
        # (правила удалятся каскадом). Иначе в БД могли бы остаться пересекающиеся периоды.
        await session.execute(
            delete(CardTerms).where(
                CardTerms.card_id == card_id,
                CardTerms.version.not_in([t.version for t in card.terms]),
            )
        )

        for terms in card.terms:
            t_values = {
                "card_id": card_id,
                "version": terms.version,
                "valid_from": terms.valid_from,
                "valid_to": terms.valid_to,
                "checked_at": terms.checked_at,
                "source": terms.source,
            }
            t_stmt = (
                insert(CardTerms)
                .values(**t_values)
                .on_conflict_do_update(
                    constraint="uq_card_terms_card_id_version",
                    set_={
                        k: t_values[k] for k in ("valid_from", "valid_to", "checked_at", "source")
                    },
                )
                .returning(CardTerms.id)
            )
            terms_id = (await session.execute(t_stmt)).scalar_one()
            # Правила версии заменяем целиком: так удалённое из файла правило не «зависнет» в БД.
            await session.execute(delete(CashbackRule).where(CashbackRule.terms_id == terms_id))
            session.add_all(
                CashbackRule(
                    terms_id=terms_id,
                    category_code=rule.category,
                    rate_percent=rule.rate_percent,
                    monthly_limit=rule.monthly_limit,
                    min_purchase=rule.min_purchase,
                )
                for rule in terms.rules
            )
            terms_count += 1
            rules_count += len(terms.rules)

    await session.flush()
    try:
        # Проверить отложенные ограничения сейчас, а не при COMMIT: ошибку файла
        # нужно отдать как CatalogImportError, а не как сбой транзакции.
        await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    except IntegrityError as exc:
        raise CatalogImportError(
            "после импорта у банка оказались две карты с одинаковым названием "
            f"(в файле или в БД): {exc.orig}"
        ) from None
    report = ImportReport(
        banks=len(catalog.banks), cards=len(catalog.cards), terms=terms_count, rules=rules_count
    )
    logger.info("catalog imported", extra=report.__dict__)
    return report


def dump_catalog(catalog: CatalogFile, path: Path) -> None:
    path.write_text(
        json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
