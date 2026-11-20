"""Общие зависимости роутеров."""

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import get_session
from app.errors import InvalidUserIdError

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def today(settings: Settings) -> date:
    """«Сегодня» в часовом поясе сервиса. Единственное место, где читается текущее время."""
    return datetime.now(settings.timezone).date()


def month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def parse_user_id(raw: str | None) -> uuid.UUID:
    if not raw:
        raise InvalidUserIdError("Нужен заголовок X-User-Id (UUID)")
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise InvalidUserIdError("X-User-Id должен быть UUID") from None


UserIdHeader = Annotated[
    str | None,
    Header(alias="X-User-Id", description="Анонимный UUID пользователя (генерирует frontend)"),
]


def require_user_id(x_user_id: UserIdHeader = None) -> uuid.UUID:
    return parse_user_id(x_user_id)


UserIdDep = Annotated[uuid.UUID, Depends(require_user_id)]
