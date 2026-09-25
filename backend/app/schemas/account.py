"""Request/response bodies for the chart of accounts."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import AccountType


class AccountCreate(BaseModel):
    code: str = Field(min_length=1, max_length=10, pattern=r"^[0-9A-Za-z-]+$")
    name: str = Field(min_length=1, max_length=100)
    type: AccountType


class AccountUpdate(BaseModel):
    """Only the name and active flag can change. Type and code are fixed once created."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    type: AccountType
    subtype: str | None
    is_active: bool
    created_at: datetime


class AccountBalance(BaseModel):
    account_id: int
    as_of: date | None
    # Positive when the account holds its normal kind of balance (see natural_balance).
    balance_cents: int


class TrialBalanceLine(BaseModel):
    account_id: int
    code: str
    name: str
    type: AccountType
    debit_cents: int
    credit_cents: int


class TrialBalance(BaseModel):
    as_of: date | None
    lines: list[TrialBalanceLine]
    total_debit_cents: int
    total_credit_cents: int
