"""Доменные ошибки и единый формат ответа об ошибке.

Формат: {"error": {"code", "message", "details", "request_id"}}
"""

import errno
import logging
import socket
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.observability.context import get_request_id
from app.observability.metrics import APP_ERRORS

logger = logging.getLogger("app.errors")


class AppError(Exception):
    status_code: int = 400
    code: str = "bad_request"

    def __init__(self, message: str, details: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []


class InvalidUserIdError(AppError):
    status_code = 400
    code = "invalid_user_id"


class CardNotFoundError(AppError):
    status_code = 404
    code = "card_not_found"


class CardAlreadyAddedError(AppError):
    status_code = 409
    code = "card_already_added"


class UnknownCategoryError(AppError):
    status_code = 422
    code = "unknown_category"


def error_body(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": get_request_id(),
        }
    }


def error_response(
    status: int, code: str, message: str, details: list[dict[str, Any]] | None = None
) -> JSONResponse:
    APP_ERRORS.labels(code=code).inc()
    return JSONResponse(status_code=status, content=error_body(code, message, details))


# Понятные пользователю сообщения для частых ошибок валидации Pydantic.
_MESSAGES_RU = {
    "missing": "Обязательное поле",
    "extra_forbidden": "Лишнее поле",
    "greater_than": "Значение должно быть больше {gt}",
    "greater_than_equal": "Значение должно быть не меньше {ge}",
    "less_than_equal": "Значение должно быть не больше {le}",
    "decimal_parsing": "Ожидается число",
    "decimal_type": "Ожидается число",
    "decimal_max_places": "Не больше {decimal_places} знаков после запятой",
    "decimal_max_digits": "Слишком большое число",
    "int_parsing": "Ожидается целое число",
    "date_from_datetime_parsing": "Ожидается дата YYYY-MM-DD",
    "date_parsing": "Ожидается дата YYYY-MM-DD",
    "string_pattern_mismatch": "Неверный формат",
    "literal_error": "Допустимые значения: {expected}",
    "json_invalid": "Некорректный JSON",
}


def _message_ru(err: Any) -> str:
    template = _MESSAGES_RU.get(err.get("type", ""))
    if template is None:
        return str(err.get("msg", "Некорректное значение"))
    try:
        return template.format(**(err.get("ctx") or {}))
    except (KeyError, IndexError):
        return template


# Классы SQLSTATE Postgres, означающие «БД недоступна/перегружена, повторите позже»:
# 08 — ошибки соединения, 53 — нехватка ресурсов (в т. ч. too many connections),
# 57 — вмешательство оператора (cannot connect now, admin shutdown, statement timeout).
_RETRYABLE_SQLSTATE_CLASSES = ("08", "53", "57")

_NETWORK_ERRNOS = {
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.ECONNABORTED,
    errno.EHOSTUNREACH,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
    errno.EPIPE,
}


def is_connection_error(exc: DBAPIError) -> bool:
    """Ошибка БД означает недоступность (→ 503), а не баг в коде или данных (→ 500)."""
    if isinstance(exc, OperationalError | InterfaceError) or exc.connection_invalidated:
        return True
    sqlstate = getattr(exc.orig, "sqlstate", None) or ""
    return sqlstate[:2] in _RETRYABLE_SQLSTATE_CLASSES


def is_network_error(exc: OSError) -> bool:
    """Сетевой OSError (отказ, DNS, таймаут). FileNotFoundError и т. п. сюда не попадают.

    «Multiple exceptions» — обычный OSError, который asyncio бросает, когда хост
    резолвится в несколько адресов (IPv4 + IPv6) и ни к одному не подключиться.
    """
    if isinstance(exc, ConnectionError | TimeoutError | socket.gaierror):
        return True
    return exc.errno in _NETWORK_ERRNOS or str(exc).startswith("Multiple exceptions")


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {
                # loc = ("body", "amount") → "amount"
                "field": ".".join(
                    str(p) for p in err["loc"] if p not in ("body", "query", "path", "header")
                ),
                "message": _message_ru(err),
            }
            for err in exc.errors()
        ]
        message = details[0]["message"] if details else "Некорректный запрос"
        return error_response(422, "validation_error", message, details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(DBAPIError)
    async def _db_error(_: Request, exc: DBAPIError) -> JSONResponse:
        # 503 только для проблем соединения; IntegrityError/ProgrammingError — это баги → 500.
        if is_connection_error(exc):
            logger.error("database unavailable", exc_info=exc)
            return error_response(503, "service_unavailable", "База данных временно недоступна")
        logger.error("unhandled database error", exc_info=exc)
        return error_response(500, "internal_error", "Внутренняя ошибка сервера")

    @app.exception_handler(OSError)
    async def _os_error(_: Request, exc: OSError) -> JSONResponse:
        if is_network_error(exc):
            logger.error("database unavailable", exc_info=exc)
            return error_response(503, "service_unavailable", "База данных временно недоступна")
        logger.error("unhandled os error", exc_info=exc)
        return error_response(500, "internal_error", "Внутренняя ошибка сервера")

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def _pool_timeout(_: Request, exc: SQLAlchemyTimeoutError) -> JSONResponse:
        # Пул соединений исчерпан: перегрузка, клиенту стоит повторить запрос.
        logger.error("database pool exhausted", exc_info=exc)
        return error_response(503, "service_unavailable", "Сервис перегружен, повторите запрос")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Стек и request_id уходят в логи, клиенту показываем только request_id.
        logger.exception("unhandled error", exc_info=exc)
        return error_response(500, "internal_error", "Внутренняя ошибка сервера")
