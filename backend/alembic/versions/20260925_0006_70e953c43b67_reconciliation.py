"""reconciliation

Revision ID: 70e953c43b67
Revises: c13f768df49f
Create Date: 2026-09-25 00:06:32.357835
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "70e953c43b67"
down_revision: str | None = "c13f768df49f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bank_transactions", sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        op.f("uq_bank_transactions_journal_entry_id"), "bank_transactions", ["journal_entry_id"]
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_bank_transactions_journal_entry_id"), "bank_transactions", type_="unique"
    )
    op.drop_column("bank_transactions", "reconciled_at")
