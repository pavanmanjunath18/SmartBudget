"""The general ledger: journal entries and their lines.

Sign convention (used everywhere): a line's `amount_cents` is positive for a debit
and negative for a credit. A balanced entry is therefore one whose lines sum to 0.

Posted entries are immutable. Mistakes are fixed with a reversing entry.
"""

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.account import Account


class EntrySource(StrEnum):
    """What created the entry."""

    MANUAL = "manual"
    REVERSAL = "reversal"
    BANK = "bank"  # a categorized bank transaction
    INVOICE = "invoice"  # an invoice was sent: debit AR, credit income
    PAYMENT = "payment"  # a customer paid: debit bank, credit AR


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (Index("ix_journal_entries_org_date", "org_id", "entry_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    entry_date: Mapped[date] = mapped_column(Date)
    memo: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(20), default=EntrySource.MANUAL)
    # unique: an entry can be reversed at most once, even if two requests race.
    reverses_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("journal_entries.id"), unique=True
    )
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    lines: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", order_by="JournalLine.id"
    )
    reverses: Mapped["JournalEntry | None"] = relationship(
        remote_side=[id], back_populates="reversed_by"
    )
    reversed_by: Mapped["JournalEntry | None"] = relationship(back_populates="reverses")


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("amount_cents <> 0", name="amount_not_zero"),
        Index("ix_journal_lines_org_account", "org_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    description: Mapped[str] = mapped_column(String(500), default="")

    entry: Mapped[JournalEntry] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()


class ImmutableLedgerError(Exception):
    """Raised when code tries to change or delete a posted ledger row."""


def _block_change(mapper: object, connection: object, target: object) -> None:
    raise ImmutableLedgerError(
        f"{type(target).__name__} rows are immutable; post a reversing entry instead."
    )


# Safety net inside the app: any ORM update/delete of ledger rows fails loudly,
# even if a future service forgets the rule. (Bulk SQL UPDATE bypasses this;
# the codebase never issues one against these tables.)
for _model in (JournalEntry, JournalLine):
    event.listen(_model, "before_update", _block_change)
    event.listen(_model, "before_delete", _block_change)
