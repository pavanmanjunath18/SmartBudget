"""accounts and journal

Revision ID: 05aed03cd54b
Revises: 12d272ad3b73
Create Date: 2026-09-24 23:01:05.943989
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "05aed03cd54b"
down_revision: str | None = "12d272ad3b73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("subtype", sa.String(length=30), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_accounts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("org_id", "code", name="uq_accounts_org_code"),
    )
    op.create_table(
        "journal_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("memo", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("reverses_entry_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_journal_entries_created_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_journal_entries_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_entry_id"],
            ["journal_entries.id"],
            name=op.f("fk_journal_entries_reverses_entry_id_journal_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_entries")),
        sa.UniqueConstraint("reverses_entry_id", name=op.f("uq_journal_entries_reverses_entry_id")),
    )
    op.create_index(
        "ix_journal_entries_org_date", "journal_entries", ["org_id", "entry_date"], unique=False
    )
    op.create_table(
        "journal_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.CheckConstraint("amount_cents <> 0", name=op.f("ck_journal_lines_amount_not_zero")),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_journal_lines_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["journal_entries.id"],
            name=op.f("fk_journal_lines_entry_id_journal_entries"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_journal_lines_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_journal_lines")),
    )
    op.create_index(op.f("ix_journal_lines_entry_id"), "journal_lines", ["entry_id"], unique=False)
    op.create_index(
        "ix_journal_lines_org_account", "journal_lines", ["org_id", "account_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_journal_lines_org_account", table_name="journal_lines")
    op.drop_index(op.f("ix_journal_lines_entry_id"), table_name="journal_lines")
    op.drop_table("journal_lines")
    op.drop_index("ix_journal_entries_org_date", table_name="journal_entries")
    op.drop_table("journal_entries")
    op.drop_table("accounts")
