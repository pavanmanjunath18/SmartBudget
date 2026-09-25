"""Request/response bodies for journal entries.

The API speaks in debit and credit columns, like a bookkeeper would. Internally a line
is one signed number (debit positive, credit negative); the conversion happens only here.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field, StrictInt, model_validator

from app.models import JournalLine

# $10 billion in cents: far above any real entry, far below the BIGINT limit.
MAX_CENTS = 1_000_000_000_000


class JournalLineIn(BaseModel):
    account_id: int
    # StrictInt: 12.5 or "1250" are rejected rather than silently converted.
    debit_cents: StrictInt = Field(default=0, ge=0, le=MAX_CENTS)
    credit_cents: StrictInt = Field(default=0, ge=0, le=MAX_CENTS)
    description: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def exactly_one_side(self) -> "JournalLineIn":
        if (self.debit_cents > 0) == (self.credit_cents > 0):
            raise ValueError("Each line needs either a debit or a credit amount, not both.")
        return self

    @property
    def signed_cents(self) -> int:
        return self.debit_cents - self.credit_cents


class JournalEntryCreate(BaseModel):
    entry_date: date
    memo: str = Field(default="", max_length=500)
    lines: list[JournalLineIn] = Field(min_length=2)


class ReverseRequest(BaseModel):
    reversal_date: date | None = None  # defaults to today


class JournalLineOut(BaseModel):
    id: int
    account_id: int
    debit_cents: int
    credit_cents: int
    description: str

    @classmethod
    def from_line(cls, line: JournalLine) -> "JournalLineOut":
        return cls(
            id=line.id,
            account_id=line.account_id,
            debit_cents=max(line.amount_cents, 0),
            credit_cents=max(-line.amount_cents, 0),
            description=line.description,
        )


class JournalEntryOut(BaseModel):
    id: int
    entry_date: date
    memo: str
    source: str
    reverses_entry_id: int | None
    reversed_by_entry_id: int | None
    created_by_user_id: int
    created_at: datetime
    lines: list[JournalLineOut]
