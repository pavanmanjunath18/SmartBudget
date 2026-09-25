"""The demo seed runs through the real services and leaves consistent books."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BankTransaction, BankTransactionStatus, Invoice, JournalLine, User
from app.scripts.seed import DEMO_EMAIL, seed
from app.services import invoice_service, report_service
from tests.conftest import InMemoryStorage


def test_seed_builds_consistent_demo_books(db_session: Session) -> None:
    seed(db_session, InMemoryStorage())

    org_id = (
        db_session.scalars(select(User).where(User.email == DEMO_EMAIL)).one().memberships[0].org_id
    )
    ledger_total = db_session.scalar(
        select(func.sum(JournalLine.amount_cents)).where(JournalLine.org_id == org_id)
    )
    assert ledger_total == 0
    assert report_service.balance_sheet(db_session, org_id, date.today()).is_balanced

    statuses = dict(
        db_session.execute(
            select(BankTransaction.status, func.count())
            .where(BankTransaction.org_id == org_id)
            .group_by(BankTransaction.status)
        ).all()
    )
    assert statuses[BankTransactionStatus.MATCHED] == 2  # the two customer deposits
    assert statuses[BankTransactionStatus.FOR_REVIEW] == 5  # left for the demo on purpose

    invoices = db_session.scalars(select(Invoice).where(Invoice.org_id == org_id)).all()
    shown = sorted(invoice_service.display_status(i) for i in invoices)
    assert shown.count("paid") == 1 and shown.count("draft") == 1
    assert "overdue" in shown


def test_seed_is_idempotent(db_session: Session) -> None:
    seed(db_session, InMemoryStorage())
    lines_after_first = db_session.scalar(select(func.count()).select_from(JournalLine))

    seed(db_session, InMemoryStorage())

    assert db_session.scalar(select(func.count()).select_from(JournalLine)) == lines_after_first
