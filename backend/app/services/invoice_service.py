"""Customers, invoices, payments and the accounts-receivable aging report.

Ledger effects (all through ledger_service.record_entry):
- sending an invoice:  debit Accounts Receivable, credit each line's income account
- recording a payment: debit the deposit (bank) account, credit Accounts Receivable
Drafts never touch the ledger, so they can be edited or deleted freely.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Account,
    AccountSubtype,
    AccountType,
    Customer,
    EntrySource,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Payment,
)
from app.services import ledger_service
from app.services.errors import BusinessRuleError, ConflictError, NotFoundError
from app.services.ledger_service import LineInput

logger = logging.getLogger(__name__)

OVERDUE = "overdue"


# --- Customers -----------------------------------------------------------------------


def list_customers(db: Session, org_id: int) -> list[Customer]:
    return list(
        db.scalars(select(Customer).where(Customer.org_id == org_id).order_by(Customer.name))
    )


def get_customer(db: Session, org_id: int, customer_id: int) -> Customer:
    customer = db.scalars(
        select(Customer).where(Customer.org_id == org_id, Customer.id == customer_id)
    ).first()
    if customer is None:
        raise NotFoundError("Customer not found.")
    return customer


def create_customer(db: Session, org_id: int, name: str, email: str | None) -> Customer:
    customer = Customer(org_id=org_id, name=name.strip(), email=email)
    db.add(customer)
    db.commit()
    return customer


def update_customer(
    db: Session, org_id: int, customer_id: int, name: str | None, email: str | None
) -> Customer:
    customer = get_customer(db, org_id, customer_id)
    if name is not None:
        customer.name = name.strip()
    if email is not None:
        customer.email = email
    db.commit()
    return customer


# --- Invoices ------------------------------------------------------------------------


@dataclass(frozen=True)
class LineData:
    description: str
    quantity: Decimal
    unit_price_cents: int
    income_account_id: int


def line_amount_cents(quantity: Decimal, unit_price_cents: int) -> int:
    """quantity x unit price, rounded half-up to a whole cent, once per line.

    Example: 1.5 hours at $33.33 = 49.995 -> 5000 cents ($50.00).
    """
    return int((quantity * unit_price_cents).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def display_status(invoice: Invoice, today: date | None = None) -> str:
    """The stored status, except a sent invoice past its due date shows as "overdue".

    Computed on read instead of stored, so it can never go stale and no nightly job
    is needed to flip it.
    """
    today = today or date.today()
    if (
        invoice.status == InvoiceStatus.SENT
        and invoice.due_date < today
        and invoice.balance_due_cents > 0
    ):
        return OVERDUE
    return invoice.status


def _invoice_query(org_id: int):
    return (
        select(Invoice)
        .options(
            selectinload(Invoice.lines),
            selectinload(Invoice.payments),
            selectinload(Invoice.customer),
        )
        .where(Invoice.org_id == org_id)
    )


def get_invoice(db: Session, org_id: int, invoice_id: int, lock: bool = False) -> Invoice:
    stmt = _invoice_query(org_id).where(Invoice.id == invoice_id)
    if lock:
        stmt = stmt.with_for_update(of=Invoice)
    invoice = db.scalars(stmt).first()
    if invoice is None:
        raise NotFoundError("Invoice not found.")
    return invoice


def list_invoices(
    db: Session, org_id: int, status: str | None = None, today: date | None = None
) -> list[Invoice]:
    stmt = _invoice_query(org_id).order_by(Invoice.number.desc())
    if status is not None and status != OVERDUE:
        stmt = stmt.where(Invoice.status == status)
    invoices = list(db.scalars(stmt))
    if status == OVERDUE:
        invoices = [i for i in invoices if display_status(i, today) == OVERDUE]
    return invoices


def _validate_lines(db: Session, org_id: int, lines: list[LineData]) -> None:
    if not lines:
        raise BusinessRuleError("An invoice needs at least one line.")
    account_ids = {line.income_account_id for line in lines}
    accounts = db.scalars(
        select(Account).where(Account.org_id == org_id, Account.id.in_(account_ids))
    ).all()
    if len(accounts) != len(account_ids):
        raise BusinessRuleError("Income account not found.")
    if any(a.type != AccountType.INCOME or not a.is_active for a in accounts):
        raise BusinessRuleError("Invoice lines must use an active income account.")


def _set_lines(invoice: Invoice, org_id: int, lines: list[LineData]) -> None:
    invoice.lines = [
        InvoiceLine(
            org_id=org_id,
            description=line.description,
            quantity=line.quantity,
            unit_price_cents=line.unit_price_cents,
            amount_cents=line_amount_cents(line.quantity, line.unit_price_cents),
            income_account_id=line.income_account_id,
        )
        for line in lines
    ]
    invoice.total_cents = sum(line.amount_cents for line in invoice.lines)
    if invoice.total_cents <= 0:
        raise BusinessRuleError("Invoice total must be greater than zero.")


def create_invoice(
    db: Session,
    org_id: int,
    customer_id: int,
    issue_date: date,
    due_date: date,
    memo: str,
    lines: list[LineData],
) -> Invoice:
    """Create a draft with the next invoice number for this organization.

    Numbering is "highest number + 1". If two drafts are created at the same moment
    they can pick the same number; the UNIQUE(org_id, number) constraint rejects the
    second one and it is retried once with a fresh number.
    """
    get_customer(db, org_id, customer_id)
    if due_date < issue_date:
        raise BusinessRuleError("Due date can't be before the issue date.")
    _validate_lines(db, org_id, lines)

    for attempt in (1, 2):
        next_number = (
            db.scalar(
                select(func.coalesce(func.max(Invoice.number), 0)).where(Invoice.org_id == org_id)
            )
            + 1
        )
        invoice = Invoice(
            org_id=org_id,
            customer_id=customer_id,
            number=next_number,
            issue_date=issue_date,
            due_date=due_date,
            memo=memo,
        )
        _set_lines(invoice, org_id, lines)
        db.add(invoice)
        try:
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise ConflictError("Could not assign an invoice number; try again.") from None
    logger.info(
        "invoice_created org_id=%s invoice_id=%s number=%s", org_id, invoice.id, next_number
    )
    return get_invoice(db, org_id, invoice.id)


def update_draft(
    db: Session,
    org_id: int,
    invoice_id: int,
    customer_id: int | None,
    issue_date: date | None,
    due_date: date | None,
    memo: str | None,
    lines: list[LineData] | None,
) -> Invoice:
    invoice = get_invoice(db, org_id, invoice_id, lock=True)
    if invoice.status != InvoiceStatus.DRAFT:
        raise BusinessRuleError("Only draft invoices can be edited.")
    if customer_id is not None:
        get_customer(db, org_id, customer_id)
        invoice.customer_id = customer_id
    if issue_date is not None:
        invoice.issue_date = issue_date
    if due_date is not None:
        invoice.due_date = due_date
    if invoice.due_date < invoice.issue_date:
        raise BusinessRuleError("Due date can't be before the issue date.")
    if memo is not None:
        invoice.memo = memo
    if lines is not None:
        _validate_lines(db, org_id, lines)
        _set_lines(invoice, org_id, lines)
    db.commit()
    return get_invoice(db, org_id, invoice.id)


def delete_draft(db: Session, org_id: int, invoice_id: int) -> None:
    invoice = get_invoice(db, org_id, invoice_id, lock=True)
    if invoice.status != InvoiceStatus.DRAFT:
        raise BusinessRuleError("Only draft invoices can be deleted.")
    db.delete(invoice)
    db.commit()


def _account_by_subtype(db: Session, org_id: int, subtype: AccountSubtype) -> Account:
    account = db.scalars(
        select(Account)
        .where(Account.org_id == org_id, Account.subtype == subtype, Account.is_active.is_(True))
        .order_by(Account.code)
    ).first()
    if account is None:
        raise BusinessRuleError(f"No active {subtype.replace('_', ' ')} account.")
    return account


def send_invoice(db: Session, org_id: int, user_id: int, invoice_id: int) -> Invoice:
    """Mark a draft as sent and put it on the books: debit AR, credit income per account."""
    invoice = get_invoice(db, org_id, invoice_id, lock=True)
    if invoice.status != InvoiceStatus.DRAFT:
        raise ConflictError("Only draft invoices can be sent.")
    receivable = _account_by_subtype(db, org_id, AccountSubtype.ACCOUNTS_RECEIVABLE)

    income_by_account: dict[int, int] = defaultdict(int)
    for line in invoice.lines:
        income_by_account[line.income_account_id] += line.amount_cents
    memo = f"Invoice #{invoice.number} - {invoice.customer.name}"
    entry = ledger_service.record_entry(
        db,
        org_id,
        user_id,
        invoice.issue_date,
        memo,
        [LineInput(receivable.id, invoice.total_cents, memo)]
        + [LineInput(acct, -cents, memo) for acct, cents in sorted(income_by_account.items())],
        source=EntrySource.INVOICE,
    )
    invoice.status = InvoiceStatus.SENT
    invoice.journal_entry_id = entry.id
    db.commit()
    logger.info("invoice_sent org_id=%s invoice_id=%s entry_id=%s", org_id, invoice.id, entry.id)
    return invoice


def record_payment(
    db: Session,
    org_id: int,
    user_id: int,
    invoice_id: int,
    amount_cents: int,
    payment_date: date,
    deposit_account_id: int | None = None,
) -> Invoice:
    """Record money received against a sent invoice: debit bank, credit AR.

    The invoice row is locked for the whole operation, so two payments recorded at
    the same moment can't together exceed the balance due.
    """
    invoice = get_invoice(db, org_id, invoice_id, lock=True)
    if invoice.status != InvoiceStatus.SENT:
        raise BusinessRuleError(f"Can't record a payment on a {invoice.status} invoice.")
    if amount_cents <= 0:
        raise BusinessRuleError("Payment amount must be positive.")
    if amount_cents > invoice.balance_due_cents:
        raise BusinessRuleError(
            f"Payment is more than the balance due ({invoice.balance_due_cents} cents)."
        )
    if payment_date < invoice.issue_date:
        raise BusinessRuleError("Payment can't be dated before the invoice.")

    if deposit_account_id is None:
        deposit = _account_by_subtype(db, org_id, AccountSubtype.BANK)
    else:
        deposit = db.scalars(
            select(Account).where(Account.org_id == org_id, Account.id == deposit_account_id)
        ).first()
        if deposit is None or deposit.subtype != AccountSubtype.BANK:
            raise BusinessRuleError("Payments must be deposited into a bank account.")
    receivable = _account_by_subtype(db, org_id, AccountSubtype.ACCOUNTS_RECEIVABLE)

    memo = f"Payment for invoice #{invoice.number} - {invoice.customer.name}"
    entry = ledger_service.record_entry(
        db,
        org_id,
        user_id,
        payment_date,
        memo,
        [LineInput(deposit.id, amount_cents, memo), LineInput(receivable.id, -amount_cents, memo)],
        source=EntrySource.PAYMENT,
    )
    invoice.payments.append(
        Payment(
            org_id=org_id,
            payment_date=payment_date,
            amount_cents=amount_cents,
            deposit_account_id=deposit.id,
            journal_entry_id=entry.id,
        )
    )
    if invoice.balance_due_cents == 0:
        invoice.status = InvoiceStatus.PAID
    db.commit()
    logger.info(
        "payment_recorded org_id=%s invoice_id=%s amount_cents=%s", org_id, invoice.id, amount_cents
    )
    return invoice


# --- AR aging ------------------------------------------------------------------------

AGING_BUCKETS = ["current", "1_30", "31_60", "61_90", "over_90"]


def _bucket(days_past_due: int) -> str:
    if days_past_due <= 0:
        return "current"
    if days_past_due <= 30:
        return "1_30"
    if days_past_due <= 60:
        return "31_60"
    if days_past_due <= 90:
        return "61_90"
    return "over_90"


@dataclass
class AgingRow:
    customer_id: int
    customer_name: str
    buckets: dict[str, int] = field(default_factory=lambda: dict.fromkeys(AGING_BUCKETS, 0))

    @property
    def total_cents(self) -> int:
        return sum(self.buckets.values())


def ar_aging(db: Session, org_id: int, as_of: date) -> list[AgingRow]:
    """What each customer owed on `as_of`, grouped by how late it was.

    Uses dates, not the current status: an invoice issued on or before `as_of` counts
    with its total minus payments dated on or before `as_of`. So an invoice paid next
    week still shows as open in a report for today. Drafts are never included.
    """
    invoices = db.scalars(
        _invoice_query(org_id).where(
            Invoice.status.in_([InvoiceStatus.SENT, InvoiceStatus.PAID]),
            Invoice.issue_date <= as_of,
        )
    ).all()
    rows: dict[int, AgingRow] = {}
    for invoice in invoices:
        paid = sum(p.amount_cents for p in invoice.payments if p.payment_date <= as_of)
        open_cents = invoice.total_cents - paid
        if open_cents <= 0:
            continue
        row = rows.setdefault(
            invoice.customer_id, AgingRow(invoice.customer_id, invoice.customer.name)
        )
        row.buckets[_bucket((as_of - invoice.due_date).days)] += open_cents
    return sorted(rows.values(), key=lambda r: r.customer_name)
