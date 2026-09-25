"""Customers, invoices and payments (accounts receivable)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class InvoiceStatus(StrEnum):
    """Stored statuses. "overdue" is not stored: it's computed when read (see invoice_service)."""

    DRAFT = "draft"  # editable, not in the ledger
    SENT = "sent"  # posted to the ledger, money owed
    PAID = "paid"  # fully paid


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("org_id", "number", name="uq_invoices_org_number"),
        CheckConstraint("total_cents >= 0", name="total_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default=InvoiceStatus.DRAFT)
    memo: Mapped[str] = mapped_column(String(500), default="")
    total_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    # The entry that put the invoice on the books (debit AR, credit income), set on send.
    journal_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    customer: Mapped[Customer] = relationship()
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.id"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="invoice", order_by="Payment.id"
    )

    @property
    def paid_cents(self) -> int:
        return sum(p.amount_cents for p in self.payments)

    @property
    def balance_due_cents(self) -> int:
        return self.total_cents - self.paid_cents


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(String(500))
    # Hours or units; two decimals allow "1.5 hours". Money itself stays in integer cents.
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    unit_price_cents: Mapped[int] = mapped_column(BigInteger)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    income_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (CheckConstraint("amount_cents > 0", name="amount_positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    payment_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    deposit_account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    journal_entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    invoice: Mapped[Invoice] = relationship(back_populates="payments")
