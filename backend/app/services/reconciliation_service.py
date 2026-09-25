"""Bank reconciliation: does the ledger agree with the bank statement?

Two pieces:
1. Matching. Some money is already in the ledger before the bank line is imported,
   e.g. a customer payment recorded against an invoice. Categorizing that bank line
   would count the money twice, so instead it is *matched* (linked) to the existing
   entry.
2. Reconciling. The user types the statement's ending balance. It is compared with
   the ledger balance of the bank account on that date, and the difference is
   explained by two lists: bank lines not in the ledger yet, and ledger lines the
   bank hasn't shown. A reconciliation can only be completed when the difference is 0.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountSubtype,
    BankTransaction,
    BankTransactionStatus,
    JournalEntry,
    JournalLine,
)
from app.services.account_service import account_balance
from app.services.errors import BusinessRuleError, ConflictError, NotFoundError

logger = logging.getLogger(__name__)

MATCH_WINDOW_DAYS = 7
LINKED_STATUSES = (BankTransactionStatus.POSTED, BankTransactionStatus.MATCHED)


def _bank_transaction(
    db: Session, org_id: int, transaction_id: int, lock: bool = False
) -> BankTransaction:
    stmt = select(BankTransaction).where(
        BankTransaction.org_id == org_id, BankTransaction.id == transaction_id
    )
    if lock:
        stmt = stmt.with_for_update()
    tx = db.scalars(stmt).first()
    if tx is None:
        raise NotFoundError("Bank transaction not found.")
    return tx


def _linked_entry_ids(org_id: int):
    """Entries of this organization already linked to a bank transaction."""
    return select(BankTransaction.journal_entry_id).where(
        BankTransaction.org_id == org_id, BankTransaction.journal_entry_id.is_not(None)
    )


@dataclass(frozen=True)
class MatchCandidate:
    entry: JournalEntry
    line_amount_cents: int


def match_candidates(db: Session, org_id: int, transaction_id: int) -> list[MatchCandidate]:
    """Ledger entries this bank line could be: same bank account, same amount, dated
    within 7 days, and not linked to another bank line yet. Closest date first."""
    tx = _bank_transaction(db, org_id, transaction_id)
    window = timedelta(days=MATCH_WINDOW_DAYS)
    stmt = (
        select(JournalEntry, JournalLine.amount_cents)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalEntry.org_id == org_id,
            JournalLine.account_id == tx.account_id,
            JournalLine.amount_cents == tx.amount_cents,
            JournalEntry.entry_date.between(tx.posted_date - window, tx.posted_date + window),
            JournalEntry.id.not_in(_linked_entry_ids(org_id)),
        )
    )
    rows = db.execute(stmt).all()
    rows.sort(key=lambda row: (abs((row[0].entry_date - tx.posted_date).days), row[0].id))
    return [MatchCandidate(entry, amount) for entry, amount in rows]


def match(db: Session, org_id: int, transaction_id: int, journal_entry_id: int) -> BankTransaction:
    """Link a bank line in review to an existing ledger entry instead of posting a new one."""
    tx = _bank_transaction(db, org_id, transaction_id, lock=True)
    if tx.status != BankTransactionStatus.FOR_REVIEW:
        raise ConflictError(f"Transaction is already {tx.status}.")
    has_same_line = db.scalar(
        select(func.count())
        .select_from(JournalLine)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalEntry.org_id == org_id,
            JournalEntry.id == journal_entry_id,
            JournalLine.account_id == tx.account_id,
            JournalLine.amount_cents == tx.amount_cents,
        )
    )
    if not has_same_line:
        raise BusinessRuleError("That entry has no line on this bank account for the same amount.")
    tx.status = BankTransactionStatus.MATCHED
    tx.journal_entry_id = journal_entry_id
    try:
        db.commit()
    except IntegrityError as exc:
        # The UNIQUE constraint on journal_entry_id: someone matched this entry first.
        db.rollback()
        raise ConflictError("That entry is already matched to another bank transaction.") from exc
    logger.info("bank_tx_matched org_id=%s tx_id=%s entry_id=%s", org_id, tx.id, journal_entry_id)
    return tx


def unmatch(db: Session, org_id: int, transaction_id: int) -> BankTransaction:
    """Undo a match (not a categorization: posted entries are fixed by reversing)."""
    tx = _bank_transaction(db, org_id, transaction_id, lock=True)
    if tx.status != BankTransactionStatus.MATCHED:
        raise BusinessRuleError("Only matched transactions can be unmatched.")
    if tx.reconciled_at is not None:
        raise BusinessRuleError("Reconciled transactions can't be unmatched.")
    tx.status = BankTransactionStatus.FOR_REVIEW
    tx.journal_entry_id = None
    db.commit()
    return tx


@dataclass(frozen=True)
class UnclearedLine:
    entry_id: int
    entry_date: date
    memo: str
    amount_cents: int


@dataclass
class ReconciliationSummary:
    account_id: int
    statement_date: date
    statement_balance_cents: int
    ledger_balance_cents: int
    period_start: date | None
    bank_only: list[BankTransaction]  # imported, not in the ledger yet
    ledger_only: list[UnclearedLine]  # in the ledger, not seen on the bank statement
    already_reconciled_count: int
    ready_to_reconcile_count: int

    @property
    def difference_cents(self) -> int:
        return self.statement_balance_cents - self.ledger_balance_cents


def _bank_account(db: Session, org_id: int, account_id: int) -> Account:
    account = db.scalars(
        select(Account).where(Account.org_id == org_id, Account.id == account_id)
    ).first()
    if account is None:
        raise NotFoundError("Account not found.")
    if account.subtype != AccountSubtype.BANK:
        raise BusinessRuleError("Only bank accounts can be reconciled.")
    return account


def summary(
    db: Session, org_id: int, account_id: int, statement_date: date, statement_balance_cents: int
) -> ReconciliationSummary:
    """Compare a statement ending balance with the ledger and list what explains the gap.

    `ledger_only` covers ledger lines from the first imported bank line onward: before
    any import there is no bank data to compare against (e.g. an opening balance).
    """
    account = _bank_account(db, org_id, account_id)
    in_account = (BankTransaction.org_id == org_id, BankTransaction.account_id == account.id)
    up_to_date = BankTransaction.posted_date <= statement_date

    bank_only = list(
        db.scalars(
            select(BankTransaction)
            .where(
                *in_account, up_to_date, BankTransaction.status == BankTransactionStatus.FOR_REVIEW
            )
            .order_by(BankTransaction.posted_date, BankTransaction.id)
        )
    )
    period_start = db.scalar(select(func.min(BankTransaction.posted_date)).where(*in_account))

    ledger_only: list[UnclearedLine] = []
    if period_start is not None:
        rows = db.execute(
            select(JournalEntry, JournalLine.amount_cents)
            .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
            .where(
                JournalEntry.org_id == org_id,
                JournalLine.account_id == account.id,
                JournalEntry.entry_date.between(period_start, statement_date),
                JournalEntry.id.not_in(_linked_entry_ids(org_id)),
            )
            .order_by(JournalEntry.entry_date, JournalEntry.id)
        ).all()
        ledger_only = [
            UnclearedLine(entry.id, entry.entry_date, entry.memo, amount) for entry, amount in rows
        ]

    linked_counts = dict(
        db.execute(
            select(BankTransaction.reconciled_at.is_not(None), func.count())
            .where(*in_account, up_to_date, BankTransaction.status.in_(LINKED_STATUSES))
            .group_by(BankTransaction.reconciled_at.is_not(None))
        ).all()
    )
    return ReconciliationSummary(
        account_id=account.id,
        statement_date=statement_date,
        statement_balance_cents=statement_balance_cents,
        ledger_balance_cents=account_balance(db, org_id, account.id, statement_date),
        period_start=period_start,
        bank_only=bank_only,
        ledger_only=ledger_only,
        already_reconciled_count=linked_counts.get(True, 0),
        ready_to_reconcile_count=linked_counts.get(False, 0),
    )


def complete(
    db: Session, org_id: int, account_id: int, statement_date: date, statement_balance_cents: int
) -> int:
    """Mark linked bank lines up to the statement date as reconciled. Needs a 0 difference.

    Returns how many transactions were newly marked.
    """
    result = summary(db, org_id, account_id, statement_date, statement_balance_cents)
    if result.difference_cents != 0:
        raise BusinessRuleError(
            f"Statement and ledger differ by {result.difference_cents} cents; "
            "resolve the listed items first."
        )
    to_mark = db.scalars(
        select(BankTransaction).where(
            BankTransaction.org_id == org_id,
            BankTransaction.account_id == account_id,
            BankTransaction.posted_date <= statement_date,
            BankTransaction.status.in_(LINKED_STATUSES),
            BankTransaction.reconciled_at.is_(None),
        )
    ).all()
    now = datetime.now(UTC)
    for tx in to_mark:
        tx.reconciled_at = now
    db.commit()
    logger.info(
        "reconciliation_completed org_id=%s account_id=%s statement_date=%s marked=%s",
        org_id,
        account_id,
        statement_date,
        len(to_mark),
    )
    return len(to_mark)
