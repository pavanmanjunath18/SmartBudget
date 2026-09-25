"""invoicing

Revision ID: c13f768df49f
Revises: 138e289c5f4b
Create Date: 2026-09-25 00:01:14.285272
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c13f768df49f"
down_revision: str | None = "138e289c5f4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_customers_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )
    op.create_index(op.f("ix_customers_org_id"), "customers", ["org_id"], unique=False)
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("memo", sa.String(length=500), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("journal_entry_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("total_cents >= 0", name=op.f("ck_invoices_total_not_negative")),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name=op.f("fk_invoices_customer_id_customers")
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entries.id"],
            name=op.f("fk_invoices_journal_entry_id_journal_entries"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_invoices_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint("org_id", "number", name="uq_invoices_org_number"),
    )
    op.create_index(op.f("ix_invoices_customer_id"), "invoices", ["customer_id"], unique=False)
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("income_account_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["income_account_id"],
            ["accounts.id"],
            name=op.f("fk_invoice_lines_income_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name=op.f("fk_invoice_lines_invoice_id_invoices"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_invoice_lines_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_lines")),
    )
    op.create_index(
        op.f("ix_invoice_lines_invoice_id"), "invoice_lines", ["invoice_id"], unique=False
    )
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("deposit_account_id", sa.Integer(), nullable=False),
        sa.Column("journal_entry_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("amount_cents > 0", name=op.f("ck_payments_amount_positive")),
        sa.ForeignKeyConstraint(
            ["deposit_account_id"],
            ["accounts.id"],
            name=op.f("fk_payments_deposit_account_id_accounts"),
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_payments_invoice_id_invoices")
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["journal_entries.id"],
            name=op.f("fk_payments_journal_entry_id_journal_entries"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_payments_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
    )
    op.create_index(op.f("ix_payments_invoice_id"), "payments", ["invoice_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_payments_invoice_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(op.f("ix_invoice_lines_invoice_id"), table_name="invoice_lines")
    op.drop_table("invoice_lines")
    op.drop_index(op.f("ix_invoices_customer_id"), table_name="invoices")
    op.drop_table("invoices")
    op.drop_index(op.f("ix_customers_org_id"), table_name="customers")
    op.drop_table("customers")
