"""Matching bank lines to existing ledger entries, and reconciling against a statement."""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context
from app.db.session import get_db
from app.models import BankTransaction
from app.schemas.bank import BankTransactionRead
from app.schemas.reconciliation import (
    MatchCandidateRead,
    MatchRequest,
    ReconcileRequest,
    ReconcileResult,
    ReconciliationRead,
    UnclearedLineRead,
)
from app.services import reconciliation_service

router = APIRouter(prefix="/orgs/{org_id}", tags=["reconciliation"])


@router.get(
    "/bank-transactions/{transaction_id}/match-candidates",
    response_model=list[MatchCandidateRead],
)
def match_candidates(
    transaction_id: int,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> list[MatchCandidateRead]:
    """Existing entries on the same bank account, same amount, within 7 days."""
    return [
        MatchCandidateRead(
            journal_entry_id=c.entry.id,
            entry_date=c.entry.entry_date,
            memo=c.entry.memo,
            source=c.entry.source,
            amount_cents=c.line_amount_cents,
        )
        for c in reconciliation_service.match_candidates(db, ctx.org_id, transaction_id)
    ]


@router.post("/bank-transactions/{transaction_id}/match", response_model=BankTransactionRead)
def match(
    transaction_id: int,
    body: MatchRequest,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> BankTransaction:
    return reconciliation_service.match(db, ctx.org_id, transaction_id, body.journal_entry_id)


@router.post("/bank-transactions/{transaction_id}/unmatch", response_model=BankTransactionRead)
def unmatch(
    transaction_id: int,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> BankTransaction:
    return reconciliation_service.unmatch(db, ctx.org_id, transaction_id)


@router.get("/reconciliation", response_model=ReconciliationRead)
def reconciliation_summary(
    account_id: int,
    statement_date: date,
    statement_balance_cents: int,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> ReconciliationRead:
    """Statement balance vs ledger balance, and the items that explain the difference."""
    s = reconciliation_service.summary(
        db, ctx.org_id, account_id, statement_date, statement_balance_cents
    )
    return ReconciliationRead(
        account_id=s.account_id,
        statement_date=s.statement_date,
        statement_balance_cents=s.statement_balance_cents,
        ledger_balance_cents=s.ledger_balance_cents,
        difference_cents=s.difference_cents,
        period_start=s.period_start,
        bank_only=[BankTransactionRead.model_validate(tx) for tx in s.bank_only],
        ledger_only=[
            UnclearedLineRead(
                journal_entry_id=line.entry_id,
                entry_date=line.entry_date,
                memo=line.memo,
                amount_cents=line.amount_cents,
            )
            for line in s.ledger_only
        ],
        already_reconciled_count=s.already_reconciled_count,
        ready_to_reconcile_count=s.ready_to_reconcile_count,
    )


@router.post("/reconciliation/complete", response_model=ReconcileResult)
def complete(
    body: ReconcileRequest,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> ReconcileResult:
    """Mark everything up to the statement date as reconciled. Requires a 0 difference."""
    count = reconciliation_service.complete(
        db, ctx.org_id, body.account_id, body.statement_date, body.statement_balance_cents
    )
    return ReconcileResult(reconciled_count=count)
