"""Классификация ошибок: «БД недоступна» (503) против «баг» (500)."""

import errno

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from app.errors import is_connection_error, is_network_error


class _Orig(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("orig")
        self.sqlstate = sqlstate


def _dbapi(sqlstate: str | None, cls: type[DBAPIError] = DBAPIError) -> DBAPIError:
    return cls("SELECT 1", {}, _Orig(sqlstate))


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        (_dbapi("53300"), True),  # too many connections
        (_dbapi("57P03"), True),  # cannot connect now (Postgres стартует)
        (_dbapi("57014"), True),  # statement timeout
        (_dbapi("08006"), True),  # connection failure
        (_dbapi(None, OperationalError), True),
        (_dbapi("23505", IntegrityError), False),  # unique violation — баг/данные
        (_dbapi("42P01"), False),  # undefined table — баг
        (_dbapi(None), False),
    ],
)
def test_is_connection_error(err: DBAPIError, expected: bool) -> None:
    assert is_connection_error(err) is expected


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), True),
        (TimeoutError(), True),
        (OSError("Multiple exceptions: [Errno 61] ..."), True),
        (OSError(errno.EHOSTUNREACH, "no route"), True),
        (FileNotFoundError(errno.ENOENT, "nope"), False),
        (PermissionError(errno.EACCES, "denied"), False),
    ],
)
def test_is_network_error(err: OSError, expected: bool) -> None:
    assert is_network_error(err) is expected
