"""Доменные ошибки и единый формат ответа об ошибке.

Формат: {"error": {"code", "message", "details", "request_id"}}
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
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
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        message = details[0]["message"] if details else "Некорректный запрос"
        return error_response(422, "validation_error", message, details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        return error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(OperationalError)
    @app.exception_handler(DBAPIError)
    @app.exception_handler(OSError)
    async def _db_down(_: Request, exc: Exception) -> JSONResponse:
        logger.error("database unavailable", exc_info=exc)
        return error_response(503, "service_unavailable", "База данных временно недоступна")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Стек и request_id уходят в логи, клиенту показываем только request_id.
        logger.exception("unhandled error", exc_info=exc)
        return error_response(500, "internal_error", "Внутренняя ошибка сервера")
