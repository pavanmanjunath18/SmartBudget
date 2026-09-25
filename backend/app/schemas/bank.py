"""Request/response bodies for CSV import and bank transactions."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.services.csv_service import ColumnMapping


class ColumnMappingIn(BaseModel):
    """Which CSV header holds each field. Provide `amount`, or both `debit` and `credit`."""

    date: str = Field(min_length=1)
    description: str = Field(min_length=1)
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    date_format: str | None = Field(default=None, examples=["%d/%m/%Y"])

    def to_mapping(self) -> ColumnMapping:
        return ColumnMapping(**self.model_dump())


class ImportPreview(BaseModel):
    headers: list[str]
    sample_rows: list[dict[str, Any]]
    suggested_mapping: dict[str, str]


class ImportRowError(BaseModel):
    row: int
    errors: list[str]


class ImportHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    filename: str
    file_sha256: str
    column_mapping: dict[str, Any]
    total_rows: int
    imported_count: int
    duplicate_count: int
    invalid_count: int
    errors: list[ImportRowError]
    created_at: datetime


class BankTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    import_id: int
    posted_date: date
    amount_cents: int  # positive = money in, negative = money out
    description: str
    normalized_vendor: str
    status: str
    journal_entry_id: int | None
    reconciled_at: datetime | None
