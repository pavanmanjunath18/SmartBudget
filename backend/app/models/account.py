"""Chart of accounts."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountType(StrEnum):
    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


# Accounts whose balance normally grows with debits. The others grow with credits.
DEBIT_NORMAL_TYPES = {AccountType.ASSET, AccountType.EXPENSE}


class AccountSubtype(StrEnum):
    """Marks the few accounts other features need to find (bank import, invoicing)."""

    BANK = "bank"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"


class Account(Base):
    """One account in an organization's chart of accounts, e.g. "5500 Software"."""

    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_accounts_org_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(20))
    subtype: Mapped[str | None] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def is_debit_normal(self) -> bool:
        return self.type in DEBIT_NORMAL_TYPES
