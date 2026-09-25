"""Request/response bodies for customers, invoices, payments and AR aging."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictInt

from app.schemas.journal import MAX_CENTS


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = None


class CustomerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None
    created_at: datetime


class InvoiceLineIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    # Up to 2 decimals, e.g. 1.5 hours. Sent as a string or number; stored exactly.
    quantity: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    unit_price_cents: StrictInt = Field(ge=0, le=MAX_CENTS)
    income_account_id: int


class InvoiceCreate(BaseModel):
    customer_id: int
    issue_date: date
    due_date: date
    memo: str = Field(default="", max_length=500)
    lines: list[InvoiceLineIn] = Field(min_length=1, max_length=100)


class InvoiceUpdate(BaseModel):
    """Drafts only. Any field left out stays as it is; `lines` replaces all lines."""

    customer_id: int | None = None
    issue_date: date | None = None
    due_date: date | None = None
    memo: str | None = Field(default=None, max_length=500)
    lines: list[InvoiceLineIn] | None = Field(default=None, min_length=1, max_length=100)


class InvoiceLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    quantity: Decimal
    unit_price_cents: int
    amount_cents: int
    income_account_id: int


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    payment_date: date
    amount_cents: int
    deposit_account_id: int
    journal_entry_id: int


class InvoiceRead(BaseModel):
    id: int
    number: int
    customer_id: int
    customer_name: str
    issue_date: date
    due_date: date
    status: str  # draft | sent | paid | overdue (overdue is computed)
    memo: str
    total_cents: int
    paid_cents: int
    balance_due_cents: int
    journal_entry_id: int | None
    lines: list[InvoiceLineRead]
    payments: list[PaymentRead]


class PaymentCreate(BaseModel):
    amount_cents: StrictInt = Field(gt=0, le=MAX_CENTS)
    payment_date: date
    deposit_account_id: int | None = None  # defaults to the main bank account


class AgingRowRead(BaseModel):
    customer_id: int
    customer_name: str
    current: int
    days_1_30: int
    days_31_60: int
    days_61_90: int
    over_90: int
    total_cents: int


class AgingReport(BaseModel):
    as_of: date
    rows: list[AgingRowRead]
    totals: AgingRowRead
