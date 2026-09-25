"""categorization

Revision ID: 138e289c5f4b
Revises: bc5e0a85f843
Create Date: 2026-09-24 23:56:30.693197
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "138e289c5f4b"
down_revision: str | None = "bc5e0a85f843"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "category_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("match_field", sa.String(length=20), nullable=False),
        sa.Column("match_type", sa.String(length=20), nullable=False),
        sa.Column("pattern", sa.String(length=200), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["accounts.id"], name=op.f("fk_category_rules_account_id_accounts")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_category_rules_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category_rules")),
    )
    op.create_index(
        "ix_category_rules_org_priority", "category_rules", ["org_id", "priority"], unique=False
    )
    op.create_table(
        "vendor_category_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("normalized_vendor", sa.String(length=200), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("times_confirmed", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_vendor_category_cache_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_vendor_category_cache_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_category_cache")),
        sa.UniqueConstraint("org_id", "normalized_vendor", name="uq_vendor_cache_org_vendor"),
    )
    op.create_table(
        "category_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("bank_transaction_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_category_suggestions_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["bank_transaction_id"],
            ["bank_transactions.id"],
            name=op.f("fk_category_suggestions_bank_transaction_id_bank_transactions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_category_suggestions_decided_by_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_category_suggestions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["category_rules.id"],
            name=op.f("fk_category_suggestions_rule_id_category_rules"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category_suggestions")),
    )
    op.create_index(
        op.f("ix_category_suggestions_bank_transaction_id"),
        "category_suggestions",
        ["bank_transaction_id"],
        unique=False,
    )
    op.create_index(
        "ix_suggestions_org_status", "category_suggestions", ["org_id", "status"], unique=False
    )
    op.add_column("bank_transactions", sa.Column("journal_entry_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f("fk_bank_transactions_journal_entry_id_journal_entries"),
        "bank_transactions",
        "journal_entries",
        ["journal_entry_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_bank_transactions_journal_entry_id_journal_entries"),
        "bank_transactions",
        type_="foreignkey",
    )
    op.drop_column("bank_transactions", "journal_entry_id")
    op.drop_index("ix_suggestions_org_status", table_name="category_suggestions")
    op.drop_index(
        op.f("ix_category_suggestions_bank_transaction_id"), table_name="category_suggestions"
    )
    op.drop_table("category_suggestions")
    op.drop_table("vendor_category_cache")
    op.drop_index("ix_category_rules_org_priority", table_name="category_rules")
    op.drop_table("category_rules")
