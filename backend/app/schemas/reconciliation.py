"""Request/response bodies for matching and bank reconciliation."""

from datetime import date

from pydantic import BaseModel, StrictInt

from app.schemas.bank import BankTransactionRead


class MatchCandidateRead(BaseModel):
    journal_entry_id: int
    entry_date: date
    memo: str
    source: str
    amount_cents: int


class MatchRequest(BaseModel):
    journal_entry_id: int


class UnclearedLineRead(BaseModel):
    journal_entry_id: int
    entry_date: date
    memo: str
    amount_cents: int


class ReconciliationRead(BaseModel):
    account_id: int
    statement_date: date
    statement_balance_cents: int
    ledger_balance_cents: int
    difference_cents: int  # statement - ledger; must be 0 to complete
    period_start: date | None
    bank_only: list[BankTransactionRead]
    ledger_only: list[UnclearedLineRead]
    already_reconciled_count: int
    ready_to_reconcile_count: int


class ReconcileRequest(BaseModel):
    account_id: int
    statement_date: date
    statement_balance_cents: StrictInt


class ReconcileResult(BaseModel):
    reconciled_count: int
