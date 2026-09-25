"""Import bank CSV files into bank_transactions, idempotently."""

import hashlib
import logging
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Account, AccountSubtype, BankTransaction, ImportHistory
from app.services import csv_service
from app.services.csv_service import ColumnMapping, CsvFormatError
from app.services.errors import BusinessRuleError, NotFoundError
from app.services.storage import FileStorage

logger = logging.getLogger(__name__)

# Postgres allows at most 65,535 bind parameters per statement; 8 columns x 1,000 rows
# stays well below that.
INSERT_BATCH_SIZE = 1_000


def preview(content: bytes) -> tuple[list[str], list[dict[str, str]], dict[str, str]]:
    """Headers, the first few rows and a guessed column mapping, before importing."""
    try:
        headers, rows = csv_service.read_rows(csv_service.decode(content))
    except CsvFormatError as exc:
        raise BusinessRuleError(str(exc)) from exc
    return headers, rows[:5], csv_service.guess_mapping(headers)


def _bank_account(db: Session, org_id: int, account_id: int) -> Account:
    account = db.scalars(
        select(Account).where(Account.org_id == org_id, Account.id == account_id)
    ).first()
    if account is None:
        raise NotFoundError("Account not found.")
    if account.subtype != AccountSubtype.BANK or not account.is_active:
        raise BusinessRuleError("Statements can only be imported into an active bank account.")
    return account


def import_csv(
    db: Session,
    storage: FileStorage,
    org_id: int,
    user_id: int,
    account_id: int,
    filename: str,
    content: bytes,
    mapping: ColumnMapping,
) -> ImportHistory:
    """Import a statement file.

    Valid rows are inserted with ON CONFLICT DO NOTHING on (org, account, fingerprint),
    so rows already imported (by an earlier upload, or by a request running at the
    same moment) are skipped by the database itself. Everything, including the
    history record, commits in one transaction.
    """
    account = _bank_account(db, org_id, account_id)
    try:
        headers, raw_rows = csv_service.read_rows(csv_service.decode(content))
        mapping.validate(headers)
    except CsvFormatError as exc:
        raise BusinessRuleError(str(exc)) from exc

    parsed = csv_service.parse_rows(raw_rows, mapping)
    valid = [row for row in parsed if row.is_valid]
    invalid = [row for row in parsed if not row.is_valid]

    file_sha256 = hashlib.sha256(content).hexdigest()
    storage_key = f"org-{org_id}/imports/{file_sha256}.csv"
    storage.save(storage_key, content)

    history = ImportHistory(
        org_id=org_id,
        account_id=account.id,
        filename=filename[:255],
        file_sha256=file_sha256,
        storage_key=storage_key,
        column_mapping=asdict(mapping),
        total_rows=len(parsed),
        invalid_count=len(invalid),
        errors=[{"row": r.row_number, "errors": r.errors} for r in invalid],
        created_by_user_id=user_id,
    )
    db.add(history)
    db.flush()

    inserted = 0
    if valid:
        values = [
            {
                "org_id": org_id,
                "account_id": account.id,
                "import_id": history.id,
                "posted_date": row.posted_date,
                "amount_cents": row.amount_cents,
                "description": row.description,
                "normalized_vendor": csv_service.normalize_vendor(row.description)[:200],
                "fingerprint": fp,
            }
            for row, fp in zip(valid, csv_service.fingerprints(account.id, valid), strict=True)
        ]
        for start in range(0, len(values), INSERT_BATCH_SIZE):
            stmt = (
                insert(BankTransaction)
                .values(values[start : start + INSERT_BATCH_SIZE])
                .on_conflict_do_nothing(constraint="uq_bank_tx_fingerprint")
                .returning(BankTransaction.id)
            )
            inserted += len(db.execute(stmt).all())

    history.imported_count = inserted
    history.duplicate_count = len(valid) - inserted
    db.commit()
    logger.info(
        "csv_imported org_id=%s import_id=%s total=%s imported=%s duplicates=%s invalid=%s",
        org_id,
        history.id,
        history.total_rows,
        history.imported_count,
        history.duplicate_count,
        history.invalid_count,
    )
    return history


def list_imports(db: Session, org_id: int) -> list[ImportHistory]:
    stmt = (
        select(ImportHistory)
        .where(ImportHistory.org_id == org_id)
        .order_by(ImportHistory.created_at.desc(), ImportHistory.id.desc())
    )
    return list(db.scalars(stmt))


def get_import(db: Session, org_id: int, import_id: int) -> ImportHistory:
    history = db.scalars(
        select(ImportHistory).where(ImportHistory.org_id == org_id, ImportHistory.id == import_id)
    ).first()
    if history is None:
        raise NotFoundError("Import not found.")
    return history


def list_bank_transactions(
    db: Session,
    org_id: int,
    status: str | None = None,
    account_id: int | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[BankTransaction]:
    stmt = select(BankTransaction).where(BankTransaction.org_id == org_id)
    if status is not None:
        stmt = stmt.where(BankTransaction.status == status)
    if account_id is not None:
        stmt = stmt.where(BankTransaction.account_id == account_id)
    stmt = stmt.order_by(BankTransaction.posted_date.desc(), BankTransaction.id.desc())
    return list(db.scalars(stmt.limit(limit).offset(offset)))
