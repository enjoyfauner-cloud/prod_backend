"""Уникальность (банк, название) карты проверяется отложенно (DEFERRABLE INITIALLY DEFERRED).

Нужно идемпотентному импорту: две карты банка могут обменяться названиями в одном файле.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NAME = "uq_cards_bank_id_name"


def upgrade() -> None:
    op.drop_constraint(NAME, "cards", type_="unique")
    op.create_unique_constraint(
        NAME, "cards", ["bank_id", "name"], deferrable=True, initially="DEFERRED"
    )


def downgrade() -> None:
    op.drop_constraint(NAME, "cards", type_="unique")
    op.create_unique_constraint(NAME, "cards", ["bank_id", "name"])
