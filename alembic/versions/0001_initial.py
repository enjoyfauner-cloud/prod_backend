"""Начальная схема: банки, категории, карты, версии условий, правила, «мои карты», использование.

Revision ID: 0001
Revises:
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Справочник категорий фиксирован и является частью схемы (не каталога).
CATEGORIES = [
    ("supermarkets", "Супермаркеты", 1),
    ("restaurants", "Рестораны", 2),
    ("fuel", "АЗС", 3),
    ("taxi", "Такси", 4),
    ("pharmacy", "Аптеки", 5),
    ("online", "Онлайн-покупки", 6),
    ("other", "Всё остальное", 7),
]


def upgrade() -> None:
    op.create_table(
        "banks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_banks"),
        sa.UniqueConstraint("code", name="uq_banks_code"),
    )
    categories = op.create_table(
        "categories",
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_categories"),
    )
    op.create_table(
        "cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(128), nullable=False),
        sa.Column("bank_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("promo_url", sa.String(500), nullable=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cards"),
        sa.ForeignKeyConstraint(
            ["bank_id"], ["banks.id"], name="fk_cards_bank_id_banks", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("code", name="uq_cards_code"),
        sa.UniqueConstraint("bank_id", "name", name="uq_cards_bank_id_name"),
    )
    op.create_index("ix_cards_bank_id", "cards", ["bank_id"])
    op.create_table(
        "card_terms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("checked_at", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_card_terms"),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name="fk_card_terms_card_id_cards", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("card_id", "version", name="uq_card_terms_card_id_version"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name="ck_card_terms_valid_period"
        ),
    )
    op.create_index("ix_card_terms_card_id", "card_terms", ["card_id"])
    op.create_table(
        "cashback_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("terms_id", sa.Integer(), nullable=False),
        sa.Column("category_code", sa.String(32), nullable=False),
        sa.Column("rate_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("monthly_limit", sa.Numeric(12, 2), nullable=True),
        sa.Column("min_purchase", sa.Numeric(12, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_cashback_rules"),
        sa.ForeignKeyConstraint(
            ["terms_id"],
            ["card_terms.id"],
            name="fk_cashback_rules_terms_id_card_terms",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_code"],
            ["categories.code"],
            name="fk_cashback_rules_category_code_categories",
        ),
        sa.UniqueConstraint(
            "terms_id", "category_code", name="uq_cashback_rules_terms_id_category"
        ),
        sa.CheckConstraint(
            "rate_percent >= 0 AND rate_percent <= 100", name="ck_cashback_rules_rate_range"
        ),
        sa.CheckConstraint(
            "monthly_limit IS NULL OR monthly_limit > 0", name="ck_cashback_rules_limit_positive"
        ),
        sa.CheckConstraint(
            "min_purchase IS NULL OR min_purchase >= 0",
            name="ck_cashback_rules_min_purchase_nonneg",
        ),
    )
    op.create_index("ix_cashback_rules_terms_id", "cashback_rules", ["terms_id"])
    op.create_index("ix_cashback_rules_category_code", "cashback_rules", ["category_code"])
    op.create_table(
        "user_cards",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", "card_id", name="pk_user_cards"),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name="fk_user_cards_card_id_cards", ondelete="CASCADE"
        ),
    )
    op.create_table(
        "card_usage",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("category_code", sa.String(32), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("used_cashback", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "card_id", "category_code", "period", name="pk_card_usage"
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name="fk_card_usage_card_id_cards", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["category_code"], ["categories.code"], name="fk_card_usage_category_code_categories"
        ),
        sa.CheckConstraint("used_cashback >= 0", name="ck_card_usage_used_nonneg"),
        sa.CheckConstraint(
            "period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'", name="ck_card_usage_period_format"
        ),
    )
    op.bulk_insert(
        categories,
        [{"code": c, "name": n, "sort_order": s} for c, n, s in CATEGORIES],
    )


def downgrade() -> None:
    op.drop_table("card_usage")
    op.drop_table("user_cards")
    op.drop_index("ix_cashback_rules_category_code", table_name="cashback_rules")
    op.drop_index("ix_cashback_rules_terms_id", table_name="cashback_rules")
    op.drop_table("cashback_rules")
    op.drop_index("ix_card_terms_card_id", table_name="card_terms")
    op.drop_table("card_terms")
    op.drop_index("ix_cards_bank_id", table_name="cards")
    op.drop_table("cards")
    op.drop_table("categories")
    op.drop_table("banks")
