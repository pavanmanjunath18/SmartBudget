"""bank import

Revision ID: bc5e0a85f843
Revises: 05aed03cd54b
Create Date: 2026-09-24 23:51:43.504698
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bc5e0a85f843"
down_revision: str | None = "05aed03cd54b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("column_mapping", sa.JSON(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False),
        sa.Column("invalid_count", sa.Integer(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_import_history_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_import_history_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_import_history_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_history")),
    )
    op.create_index(
        "ix_import_history_org_created", "import_history", ["org_id", "created_at"], unique=False
    )
    op.create_table(
        "bank_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("import_id", sa.Integer(), nullable=False),
        sa.Column("posted_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("normalized_vendor", sa.String(length=200), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_bank_transactions_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["import_id"],
            ["import_history.id"],
            name=op.f("fk_bank_transactions_import_id_import_history"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_bank_transactions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_transactions")),
        sa.UniqueConstraint("org_id", "account_id", "fingerprint", name="uq_bank_tx_fingerprint"),
    )
    op.create_index(
        op.f("ix_bank_transactions_normalized_vendor"),
        "bank_transactions",
        ["normalized_vendor"],
        unique=False,
    )
    op.create_index(
        "ix_bank_tx_org_status", "bank_transactions", ["org_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_bank_tx_org_status", table_name="bank_transactions")
    op.drop_index(op.f("ix_bank_transactions_normalized_vendor"), table_name="bank_transactions")
    op.drop_table("bank_transactions")
    op.drop_index("ix_import_history_org_created", table_name="import_history")
    op.drop_table("import_history")
