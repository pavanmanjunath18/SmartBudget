"""Imported bank data: one ImportHistory row per uploaded file, one BankTransaction per row."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BankTransactionStatus(StrEnum):
    FOR_REVIEW = "for_review"  # imported, not yet in the ledger
    POSTED = "posted"  # categorized and posted as a journal entry
    EXCLUDED = "excluded"  # user said to ignore it (e.g. a transfer already recorded)


class ImportHistory(Base):
    """One uploaded CSV file and what happened to its rows."""

    __tablename__ = "import_history"
    __table_args__ = (Index("ix_import_history_org_created", "org_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    column_mapping: Mapped[dict[str, Any]] = mapped_column(JSON)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    # [{"row": 5, "errors": ["Invalid date \"13/45/2026\""]}, ...]
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BankTransaction(Base):
    """One line from a bank statement. amount_cents: positive = money in, negative = out."""

    __tablename__ = "bank_transactions"
    __table_args__ = (
        # The idempotency guarantee: the same row can't be stored twice for an account.
        UniqueConstraint("org_id", "account_id", "fingerprint", name="uq_bank_tx_fingerprint"),
        Index("ix_bank_tx_org_status", "org_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    import_id: Mapped[int] = mapped_column(ForeignKey("import_history.id"))
    posted_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(String(500))
    normalized_vendor: Mapped[str] = mapped_column(String(200), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default=BankTransactionStatus.FOR_REVIEW)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
