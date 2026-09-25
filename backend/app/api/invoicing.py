"""Customers, invoices, payments and the AR aging report."""

from datetime import date

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context
from app.db.session import get_db
from app.models import Customer, Invoice
from app.schemas.invoicing import (
    AgingReport,
    AgingRowRead,
    CustomerCreate,
    CustomerRead,
    CustomerUpdate,
    InvoiceCreate,
    InvoiceLineIn,
    InvoiceLineRead,
    InvoiceRead,
    InvoiceUpdate,
    PaymentCreate,
    PaymentRead,
)
from app.services import invoice_service
from app.services.invoice_service import AgingRow, LineData

router = APIRouter(prefix="/orgs/{org_id}", tags=["invoicing"])


def to_invoice_read(invoice: Invoice) -> InvoiceRead:
    return InvoiceRead(
        id=invoice.id,
        number=invoice.number,
        customer_id=invoice.customer_id,
        customer_name=invoice.customer.name,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        status=invoice_service.display_status(invoice),
        memo=invoice.memo,
        total_cents=invoice.total_cents,
        paid_cents=invoice.paid_cents,
        balance_due_cents=invoice.balance_due_cents,
        journal_entry_id=invoice.journal_entry_id,
        lines=[InvoiceLineRead.model_validate(line) for line in invoice.lines],
        payments=[PaymentRead.model_validate(p) for p in invoice.payments],
    )


def _line_data(lines: list[InvoiceLineIn]) -> list[LineData]:
    return [
        LineData(line.description, line.quantity, line.unit_price_cents, line.income_account_id)
        for line in lines
    ]


# --- Customers ---


@router.get("/customers", response_model=list[CustomerRead])
def list_customers(
    ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> list[Customer]:
    return invoice_service.list_customers(db, ctx.org_id)


@router.post("/customers", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(
    body: CustomerCreate, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> Customer:
    return invoice_service.create_customer(db, ctx.org_id, body.name, body.email)


@router.get("/customers/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: int, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> Customer:
    return invoice_service.get_customer(db, ctx.org_id, customer_id)


@router.patch("/customers/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: int,
    body: CustomerUpdate,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> Customer:
    return invoice_service.update_customer(db, ctx.org_id, customer_id, body.name, body.email)


# --- Invoices ---


@router.get("/invoices", response_model=list[InvoiceRead])
def list_invoices(
    status: str | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> list[InvoiceRead]:
    """Filter by draft, sent, paid or overdue."""
    return [to_invoice_read(i) for i in invoice_service.list_invoices(db, ctx.org_id, status)]


@router.post("/invoices", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def create_invoice(
    body: InvoiceCreate, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> InvoiceRead:
    invoice = invoice_service.create_invoice(
        db,
        ctx.org_id,
        body.customer_id,
        body.issue_date,
        body.due_date,
        body.memo,
        _line_data(body.lines),
    )
    return to_invoice_read(invoice)


@router.get("/invoices/{invoice_id}", response_model=InvoiceRead)
def get_invoice(
    invoice_id: int, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> InvoiceRead:
    return to_invoice_read(invoice_service.get_invoice(db, ctx.org_id, invoice_id))


@router.patch("/invoices/{invoice_id}", response_model=InvoiceRead)
def update_invoice(
    invoice_id: int,
    body: InvoiceUpdate,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> InvoiceRead:
    invoice = invoice_service.update_draft(
        db,
        ctx.org_id,
        invoice_id,
        body.customer_id,
        body.issue_date,
        body.due_date,
        body.memo,
        _line_data(body.lines) if body.lines is not None else None,
    )
    return to_invoice_read(invoice)


@router.delete("/invoices/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(
    invoice_id: int, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> Response:
    invoice_service.delete_draft(db, ctx.org_id, invoice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invoices/{invoice_id}/send", response_model=InvoiceRead)
def send_invoice(
    invoice_id: int, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> InvoiceRead:
    """Mark as sent and post it: debit Accounts Receivable, credit income."""
    return to_invoice_read(invoice_service.send_invoice(db, ctx.org_id, ctx.user.id, invoice_id))


@router.post("/invoices/{invoice_id}/payments", response_model=InvoiceRead)
def record_payment(
    invoice_id: int,
    body: PaymentCreate,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> InvoiceRead:
    """Record money received: debit bank, credit Accounts Receivable."""
    invoice = invoice_service.record_payment(
        db,
        ctx.org_id,
        ctx.user.id,
        invoice_id,
        body.amount_cents,
        body.payment_date,
        body.deposit_account_id,
    )
    return to_invoice_read(invoice)


# --- AR aging ---


def _aging_read(row: AgingRow) -> AgingRowRead:
    b = row.buckets
    return AgingRowRead(
        customer_id=row.customer_id,
        customer_name=row.customer_name,
        current=b["current"],
        days_1_30=b["1_30"],
        days_31_60=b["31_60"],
        days_61_90=b["61_90"],
        over_90=b["over_90"],
        total_cents=row.total_cents,
    )


@router.get("/reports/ar-aging", response_model=AgingReport)
def ar_aging(
    as_of: date | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> AgingReport:
    """What customers owed on `as_of` (default today), by days past due."""
    as_of = as_of or date.today()
    rows = invoice_service.ar_aging(db, ctx.org_id, as_of)
    totals = AgingRow(customer_id=0, customer_name="Total")
    for row in rows:
        for bucket, cents in row.buckets.items():
            totals.buckets[bucket] += cents
    return AgingReport(as_of=as_of, rows=[_aging_read(r) for r in rows], totals=_aging_read(totals))
