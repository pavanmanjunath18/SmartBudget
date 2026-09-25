"""Bank statement CSV upload, import history and the imported transactions."""

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context
from app.db.session import get_db
from app.models import BankTransaction, BankTransactionStatus, ImportHistory
from app.schemas.bank import (
    BankTransactionRead,
    ColumnMappingIn,
    ImportHistoryRead,
    ImportPreview,
)
from app.services import import_service
from app.services.csv_service import MAX_FILE_BYTES
from app.services.errors import BusinessRuleError
from app.services.storage import FileStorage, get_storage

router = APIRouter(prefix="/orgs/{org_id}", tags=["import"])


def _read_upload(file: UploadFile) -> bytes:
    # Read one byte past the limit so an oversized file is detected without loading it all.
    content = file.file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise BusinessRuleError("File is larger than 5 MB.")
    return content


@router.post("/imports/preview", response_model=ImportPreview)
def preview_import(
    file: UploadFile = File(...),
    ctx: OrgContext = Depends(get_org_context),
) -> ImportPreview:
    """Show headers, sample rows and a suggested column mapping. Nothing is saved."""
    headers, rows, guess = import_service.preview(_read_upload(file))
    return ImportPreview(headers=headers, sample_rows=rows, suggested_mapping=guess)


@router.post("/imports", response_model=ImportHistoryRead, status_code=status.HTTP_201_CREATED)
def create_import(
    file: UploadFile = File(...),
    account_id: int = Form(...),
    mapping: str = Form(..., description='JSON, e.g. {"date": "Date", ...}'),
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> ImportHistory:
    """Import a statement. Re-importing the same rows adds nothing new."""
    try:
        column_mapping = ColumnMappingIn.model_validate_json(mapping).to_mapping()
    except ValidationError as exc:
        raise BusinessRuleError(f"Invalid column mapping: {exc.errors()[0]['msg']}") from exc
    return import_service.import_csv(
        db,
        storage,
        ctx.org_id,
        ctx.user.id,
        account_id,
        file.filename or "upload.csv",
        _read_upload(file),
        column_mapping,
    )


@router.get("/imports", response_model=list[ImportHistoryRead])
def list_imports(
    ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> list[ImportHistory]:
    return import_service.list_imports(db, ctx.org_id)


@router.get("/imports/{import_id}", response_model=ImportHistoryRead)
def get_import(
    import_id: int,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> ImportHistory:
    return import_service.get_import(db, ctx.org_id, import_id)


@router.get("/bank-transactions", response_model=list[BankTransactionRead])
def list_bank_transactions(
    status: BankTransactionStatus | None = None,
    account_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> list[BankTransaction]:
    return import_service.list_bank_transactions(db, ctx.org_id, status, account_id, limit, offset)
