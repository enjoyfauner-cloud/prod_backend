"""ORM-модели. Деньги и ставки хранятся в NUMERIC (Decimal), не в float."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(12, 2)
PERCENT = Numeric(5, 2)

# Предсказуемые имена ограничений: миграции вниз/вверх не зависят от автогенерации имён.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Bank(Base):
    __tablename__ = "banks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))

    cards: Mapped[list["Card"]] = relationship(back_populates="bank")


class Category(Base):
    __tablename__ = "categories"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer)


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        # DEFERRABLE: при импорте проверяется в конце транзакции (карты могут обменяться
        # названиями), в остальных случаях ведёт себя как обычное ограничение.
        UniqueConstraint(
            "bank_id",
            "name",
            name="uq_cards_bank_id_name",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True)
    bank_id: Mapped[int] = mapped_column(ForeignKey("banks.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    promo_url: Mapped[str | None] = mapped_column(String(500))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)

    bank: Mapped[Bank] = relationship(back_populates="cards")
    terms: Mapped[list["CardTerms"]] = relationship(
        back_populates="card", cascade="all, delete-orphan", order_by="CardTerms.version"
    )


class CardTerms(Base):
    """Версия условий карты с периодом действия."""

    __tablename__ = "card_terms"
    __table_args__ = (
        UniqueConstraint("card_id", "version", name="uq_card_terms_card_id_version"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    checked_at: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(Text, default="")

    card: Mapped[Card] = relationship(back_populates="terms")
    rules: Mapped[list["CashbackRule"]] = relationship(
        back_populates="terms", cascade="all, delete-orphan", order_by="CashbackRule.category_code"
    )


class CashbackRule(Base):
    """Условие кэшбэка: версия условий × категория. Категория 'other' означает базовую ставку."""

    __tablename__ = "cashback_rules"
    __table_args__ = (
        UniqueConstraint("terms_id", "category_code", name="uq_cashback_rules_terms_id_category"),
        CheckConstraint("rate_percent >= 0 AND rate_percent <= 100", name="rate_range"),
        CheckConstraint("monthly_limit IS NULL OR monthly_limit > 0", name="limit_positive"),
        CheckConstraint("min_purchase IS NULL OR min_purchase >= 0", name="min_purchase_nonneg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    terms_id: Mapped[int] = mapped_column(
        ForeignKey("card_terms.id", ondelete="CASCADE"), index=True
    )
    category_code: Mapped[str] = mapped_column(ForeignKey("categories.code"), index=True)
    rate_percent: Mapped[Decimal] = mapped_column(PERCENT)
    monthly_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    min_purchase: Mapped[Decimal | None] = mapped_column(MONEY)

    terms: Mapped[CardTerms] = relationship(back_populates="rules")


class UserCard(Base):
    """«Моя карта»: только анонимный UUID и ссылка на карту каталога, без реквизитов."""

    __tablename__ = "user_cards"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    card: Mapped[Card] = relationship()


class CardUsage(Base):
    """Сколько кэшбэка по категории пользователь уже получил за месяц."""

    __tablename__ = "card_usage"
    __table_args__ = (
        CheckConstraint("used_cashback >= 0", name="used_nonneg"),
        CheckConstraint("period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="period_format"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    category_code: Mapped[str] = mapped_column(ForeignKey("categories.code"), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    used_cashback: Mapped[Decimal] = mapped_column(MONEY)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


CATEGORIES: list[tuple[str, str]] = [
    ("supermarkets", "Супермаркеты"),
    ("restaurants", "Рестораны"),
    ("fuel", "АЗС"),
    ("taxi", "Такси"),
    ("pharmacy", "Аптеки"),
    ("online", "Онлайн-покупки"),
    ("other", "Всё остальное"),
]
BASE_CATEGORY = "other"
