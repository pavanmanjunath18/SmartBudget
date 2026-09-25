"""Chart of accounts, account balances and the trial balance."""

from datetime import date

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context
from app.db.session import get_db
from app.models import Account
from app.schemas.account import (
    AccountBalance,
    AccountCreate,
    AccountRead,
    AccountUpdate,
    TrialBalance,
    TrialBalanceLine,
)
from app.services import account_service

router = APIRouter(prefix="/orgs/{org_id}", tags=["accounts"])


@router.get("/accounts", response_model=list[AccountRead])
def list_accounts(
    ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> list[Account]:
    return account_service.list_accounts(db, ctx.org_id)


@router.post("/accounts", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
def create_account(
    body: AccountCreate,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> Account:
    return account_service.create_account(db, ctx.org_id, body.code, body.name, body.type)


@router.patch("/accounts/{account_id}", response_model=AccountRead)
def update_account(
    account_id: int,
    body: AccountUpdate,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> Account:
    return account_service.update_account(db, ctx.org_id, account_id, body.name, body.is_active)


@router.get("/accounts/{account_id}/balance", response_model=AccountBalance)
def get_account_balance(
    account_id: int,
    as_of: date | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> AccountBalance:
    """Balance up to and including `as_of` (default: all time)."""
    account = account_service.get_account(db, ctx.org_id, account_id)
    raw = account_service.account_balance(db, ctx.org_id, account.id, as_of)
    return AccountBalance(
        account_id=account.id,
        as_of=as_of,
        balance_cents=account_service.natural_balance(account, raw),
    )


@router.get("/trial-balance", response_model=TrialBalance)
def get_trial_balance(
    as_of: date | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> TrialBalance:
    """Every account's balance in a debit or credit column; the totals must match."""
    rows = account_service.trial_balance(db, ctx.org_id, as_of)
    lines = [
        TrialBalanceLine(
            account_id=r.account.id,
            code=r.account.code,
            name=r.account.name,
            type=r.account.type,
            debit_cents=r.debit_cents,
            credit_cents=r.credit_cents,
        )
        for r in rows
    ]
    return TrialBalance(
        as_of=as_of,
        lines=lines,
        total_debit_cents=sum(line.debit_cents for line in lines),
        total_credit_cents=sum(line.credit_cents for line in lines),
    )
