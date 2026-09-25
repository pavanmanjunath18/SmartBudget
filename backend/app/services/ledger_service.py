"""The general ledger: posting journal entries and reversing them.

This is the only module that creates JournalEntry/JournalLine rows. Other features
(bank import, invoices, payments) will call `record_entry` so every financial event
goes through the same checks.
"""

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Account, EntrySource, JournalEntry, JournalLine
from app.services.errors import BusinessRuleError, ConflictError, NotFoundError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LineInput:
    """One line to post. amount_cents: positive = debit, negative = credit."""

    account_id: int
    amount_cents: int
    description: str = ""


def validate_lines(db: Session, org_id: int, lines: list[LineInput]) -> None:
    """Enforce double-entry rules before anything is written.

    - at least two lines
    - no zero-amount lines
    - debits equal credits (the signed amounts sum to exactly 0)
    - every account belongs to this organization and is active
    """
    if len(lines) < 2:
        raise BusinessRuleError("A journal entry needs at least two lines.")
    if any(line.amount_cents == 0 for line in lines):
        raise BusinessRuleError("Journal lines can't have a zero amount.")
    total = sum(line.amount_cents for line in lines)
    if total != 0:
        raise BusinessRuleError(f"Debits and credits must be equal (off by {abs(total)} cents).")

    account_ids = {line.account_id for line in lines}
    accounts = db.scalars(
        select(Account).where(Account.org_id == org_id, Account.id.in_(account_ids))
    ).all()
    found = {a.id: a for a in accounts}
    missing = account_ids - found.keys()
    if missing:
        # Accounts of other organizations are reported as "not found", same as ids
        # that don't exist, so this can't be used to probe another org's data.
        raise BusinessRuleError(f"Account(s) not found: {sorted(missing)}.")
    inactive = sorted(a.code for a in accounts if not a.is_active)
    if inactive:
        raise BusinessRuleError(f"Account(s) are inactive: {inactive}.")


def record_entry(
    db: Session,
    org_id: int,
    user_id: int,
    entry_date: date,
    memo: str,
    lines: list[LineInput],
    source: EntrySource = EntrySource.MANUAL,
    reverses_entry_id: int | None = None,
) -> JournalEntry:
    """Validate and add a journal entry to the session. Does not commit.

    Callers that post as part of a bigger change (e.g. marking an invoice paid)
    commit once at the end, so the entry and the change succeed or fail together.
    """
    validate_lines(db, org_id, lines)
    entry = JournalEntry(
        org_id=org_id,
        entry_date=entry_date,
        memo=memo,
        source=source,
        reverses_entry_id=reverses_entry_id,
        created_by_user_id=user_id,
        lines=[
            JournalLine(
                org_id=org_id,
                account_id=line.account_id,
                amount_cents=line.amount_cents,
                description=line.description,
            )
            for line in lines
        ],
    )
    db.add(entry)
    db.flush()
    logger.info(
        "journal_entry_recorded org_id=%s entry_id=%s source=%s lines=%s",
        org_id,
        entry.id,
        source,
        len(lines),
    )
    return entry


def post_entry(
    db: Session, org_id: int, user_id: int, entry_date: date, memo: str, lines: list[LineInput]
) -> JournalEntry:
    """Post a manual journal entry and commit."""
    entry = record_entry(db, org_id, user_id, entry_date, memo, lines)
    db.commit()
    return entry


def get_entry(db: Session, org_id: int, entry_id: int) -> JournalEntry:
    """One entry of this organization with its lines, or NotFoundError."""
    entry = db.scalars(
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines), selectinload(JournalEntry.reversed_by))
        .where(JournalEntry.org_id == org_id, JournalEntry.id == entry_id)
    ).first()
    if entry is None:
        raise NotFoundError("Journal entry not found.")
    return entry


def list_entries(
    db: Session,
    org_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[JournalEntry]:
    """Entries of this organization, newest first. Lines are loaded in bulk (no N+1)."""
    stmt = (
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines), selectinload(JournalEntry.reversed_by))
        .where(JournalEntry.org_id == org_id)
        .order_by(JournalEntry.entry_date.desc(), JournalEntry.id.desc())
        .limit(limit)
        .offset(offset)
    )
    if date_from is not None:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(JournalEntry.entry_date <= date_to)
    return list(db.scalars(stmt))


def reverse_entry(
    db: Session,
    org_id: int,
    user_id: int,
    entry_id: int,
    reversal_date: date | None = None,
) -> JournalEntry:
    """Cancel a posted entry by posting its mirror image (every line's sign flipped).

    The original stays untouched, so the history shows both the mistake and the fix.
    """
    original = get_entry(db, org_id, entry_id)
    if original.source == EntrySource.REVERSAL:
        raise BusinessRuleError("A reversal can't be reversed; post a new entry instead.")
    if original.reversed_by is not None:
        raise ConflictError("This entry has already been reversed.")
    reversal_date = reversal_date or date.today()
    if reversal_date < original.entry_date:
        raise BusinessRuleError("A reversal can't be dated before the entry it reverses.")

    mirror = [
        LineInput(line.account_id, -line.amount_cents, line.description) for line in original.lines
    ]
    try:
        reversal = record_entry(
            db,
            org_id,
            user_id,
            reversal_date,
            memo=f"Reversal of entry #{original.id}",
            lines=mirror,
            source=EntrySource.REVERSAL,
            reverses_entry_id=original.id,
        )
    except IntegrityError as exc:
        # Another request reversed it first; the unique constraint caught the race.
        db.rollback()
        raise ConflictError("This entry has already been reversed.") from exc
    db.commit()
    return reversal
