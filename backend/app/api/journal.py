"""Journal entries. There is deliberately no update or delete route: entries are immutable."""

from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context
from app.db.session import get_db
from app.models import JournalEntry
from app.schemas.journal import (
    JournalEntryCreate,
    JournalEntryOut,
    JournalLineOut,
    ReverseRequest,
)
from app.services import ledger_service
from app.services.ledger_service import LineInput

router = APIRouter(prefix="/orgs/{org_id}/journal-entries", tags=["journal"])


def to_entry_out(entry: JournalEntry) -> JournalEntryOut:
    return JournalEntryOut(
        id=entry.id,
        entry_date=entry.entry_date,
        memo=entry.memo,
        source=entry.source,
        reverses_entry_id=entry.reverses_entry_id,
        reversed_by_entry_id=entry.reversed_by.id if entry.reversed_by else None,
        created_by_user_id=entry.created_by_user_id,
        created_at=entry.created_at,
        lines=[JournalLineOut.from_line(line) for line in entry.lines],
    )


@router.post("", response_model=JournalEntryOut, status_code=status.HTTP_201_CREATED)
def create_entry(
    body: JournalEntryCreate,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> JournalEntryOut:
    lines = [LineInput(line.account_id, line.signed_cents, line.description) for line in body.lines]
    entry = ledger_service.post_entry(
        db, ctx.org_id, ctx.user.id, body.entry_date, body.memo, lines
    )
    return to_entry_out(entry)


@router.get("", response_model=list[JournalEntryOut])
def list_entries(
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> list[JournalEntryOut]:
    entries = ledger_service.list_entries(db, ctx.org_id, date_from, date_to, limit, offset)
    return [to_entry_out(e) for e in entries]


@router.get("/{entry_id}", response_model=JournalEntryOut)
def get_entry(
    entry_id: int,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> JournalEntryOut:
    return to_entry_out(ledger_service.get_entry(db, ctx.org_id, entry_id))


@router.post(
    "/{entry_id}/reverse", response_model=JournalEntryOut, status_code=status.HTTP_201_CREATED
)
def reverse_entry(
    entry_id: int,
    body: ReverseRequest | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> JournalEntryOut:
    """Post a mirror-image entry that cancels this one. The original is kept."""
    reversal_date = body.reversal_date if body else None
    reversal = ledger_service.reverse_entry(db, ctx.org_id, ctx.user.id, entry_id, reversal_date)
    return to_entry_out(reversal)
