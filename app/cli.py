"""CLI: python -m app.cli import-catalog data/demo_catalog.json"""

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import DataError, IntegrityError, SQLAlchemyError

from app.config import get_settings
from app.db import make_engine, make_sessionmaker
from app.observability.logging import configure_logging
from app.services.catalog_import import CatalogImportError, import_catalog, load_catalog_file


async def _import(path: Path) -> int:
    try:
        catalog = load_catalog_file(path)
    except ValidationError as exc:
        print(f"Файл {path} не прошёл проверку:\n{exc}", file=sys.stderr)
        return 2
    except (KeyError, ValueError, csv.Error) as exc:  # нет колонки, не число, NUL-байт…
        print(f"Файл {path} не прошёл проверку: {exc!r}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Не удалось прочитать {path}: {exc}", file=sys.stderr)
        return 2
    settings = get_settings()
    engine = make_engine(settings)
    try:
        async with make_sessionmaker(engine)() as session, session.begin():
            report = await import_catalog(session, catalog)
    except CatalogImportError as exc:
        print(f"Импорт отменён: {exc}", file=sys.stderr)
        return 2
    except (IntegrityError, DataError) as exc:
        # Данные файла нарушили ограничение БД — это проблема файла, а не БД.
        print(f"Импорт отменён, данные файла нарушают ограничения БД: {exc.orig}", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError) as exc:
        print(f"Импорт отменён, ошибка БД: {exc}", file=sys.stderr)
        return 3
    finally:
        await engine.dispose()
    print(
        f"OK: банков {report.banks}, карт {report.cards}, "
        f"версий условий {report.terms}, правил {report.rules}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import-catalog", help="Идемпотентно загрузить справочник (JSON/CSV)")
    imp.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)
    if args.command == "import-catalog":
        return asyncio.run(_import(args.path))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
