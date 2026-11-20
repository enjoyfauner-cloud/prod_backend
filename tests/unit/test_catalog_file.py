"""Валидация файла справочника (без БД)."""

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.catalog_file import CatalogFile
from app.services.catalog_import import load_catalog_file
from scripts.generate_catalog import build

ROOT = Path(__file__).resolve().parents[2]


def minimal() -> dict[str, Any]:
    return {
        "banks": [{"code": "b", "name": "Б"}],
        "cards": [
            {
                "code": "c1",
                "bank": "b",
                "name": "Карта",
                "terms": [
                    {
                        "version": 1,
                        "valid_from": "2026-01-01",
                        "valid_to": "2026-06-30",
                        "checked_at": "2026-01-01",
                        "rules": [{"category": "other", "rate_percent": "1.00"}],
                    }
                ],
            }
        ],
    }


def test_minimal_is_valid() -> None:
    CatalogFile.model_validate(minimal())


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d["cards"][0].update(bank="nope"), "неизвестный банк"),
        (lambda d: d["cards"].append(copy.deepcopy(d["cards"][0])), "повторяется"),
        (lambda d: d["cards"][0]["terms"][0].update(valid_to="2025-01-01"), "раньше"),
        (lambda d: d["cards"][0]["terms"][0]["rules"][0].update(rate_percent="101"), "100"),
        (lambda d: d["cards"][0]["terms"][0]["rules"][0].update(rate_percent="1.234"), "decimal"),
        (lambda d: d["cards"][0]["terms"][0].update(rules=[]), "at least 1"),
        (lambda d: d["cards"][0].update(cvv="123"), "Extra inputs"),
        (
            lambda d: d["cards"][0]["terms"].append(
                {
                    **copy.deepcopy(d["cards"][0]["terms"][0]),
                    "version": 2,
                    "valid_from": "2026-06-01",
                }
            ),
            "пересекаются",
        ),
        (
            lambda d: d["cards"][0]["terms"][0]["rules"].append(
                {"category": "other", "rate_percent": "2"}
            ),
            "дважды",
        ),
    ],
)
def test_invalid_files_rejected(mutate: Any, fragment: str) -> None:
    data = minimal()
    mutate(data)
    with pytest.raises(ValidationError, match=fragment):
        CatalogFile.model_validate(data)


def test_demo_catalog_valid_and_matches_generator() -> None:
    demo = load_catalog_file(ROOT / "data" / "demo_catalog.json")
    assert 40 <= len(demo.cards) <= 60
    assert demo.meta.demo is True
    assert demo == build(42, 42)  # файл в репозитории воспроизводится генератором


def test_large_catalog_generation() -> None:
    assert len(build(600, 7).cards) == 608


def test_csv_import_format(tmp_path: Path) -> None:
    csv_file = tmp_path / "c.csv"
    csv_file.write_text(
        "bank_code,bank_name,card_code,card_name,description,promo_url,version,valid_from,"
        "valid_to,checked_at,category,rate_percent,monthly_limit,min_purchase\n"
        "b,Б,c1,Карта,,,1,2026-01-01,,2026-01-01,supermarkets,5.00,3000.00,\n"
        "b,Б,c1,Карта,,,1,2026-01-01,,2026-01-01,other,1.00,,\n",
        encoding="utf-8",
    )
    catalog = load_catalog_file(csv_file)
    assert len(catalog.cards) == 1
    assert len(catalog.cards[0].terms[0].rules) == 2


@pytest.mark.parametrize(
    "content",
    [
        "bank_code,card_code\nb,c1\n",  # нет обязательных колонок
        "bank_code,bank_name,card_code,card_name,version,valid_from,checked_at,category,"
        "rate_percent\nb,Б,c1,К,v1,2026-01-01,2026-01-01,other,1\n",  # версия не число
        "bank_code,bank_name\nb,Б\x00\n",  # NUL-байт → csv.Error
    ],
)
def test_cli_rejects_broken_csv_without_traceback(
    tmp_path: Path, content: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.cli import main

    path = tmp_path / "bad.csv"
    path.write_text(content, encoding="utf-8")
    assert main(["import-catalog", str(path)]) == 2
    assert "не прошёл проверку" in capsys.readouterr().err


def test_cli_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from app.cli import main

    assert main(["import-catalog", str(tmp_path / "nope.json")]) == 2
    assert "Не удалось прочитать" in capsys.readouterr().err
