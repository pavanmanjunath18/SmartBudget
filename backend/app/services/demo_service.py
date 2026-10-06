"""The shared demo account: build it, and reset it back to a clean state.

Everything goes through the same service functions the API uses, so the demo data obeys
every ledger rule (balanced entries, dedupe, locking). Dates are relative to today, so the
dashboard always shows recent months.

- `seed_demo` creates the demo user and a fully populated business (and does nothing if the
  user already exists). Used by `python -m app.scripts.seed` and the deploy build step.
- `reset_demo` throws away everything the demo user owns and rebuilds it. Behind the
  "Reset demo data" button, so visitors who uploaded sample files can start over.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import (
    Account,
    BankTransaction,
    BankTransactionStatus,
    MatchField,
    Membership,
    Organization,
    User,
)
from app.services import (
    auth_service,
    categorization_service,
    import_service,
    invoice_service,
    ledger_service,
    org_service,
    reconciliation_service,
)
from app.services.ai_service import NoLLMSuggester
from app.services.csv_service import ColumnMapping
from app.services.invoice_service import LineData
from app.services.ledger_service import LineInput
from app.services.storage import FileStorage

logger = logging.getLogger("demo")

DEMO_EMAIL = "demo@smartbudget.dev"
# A throwaway password for a public demo account. Never reuse it anywhere real.
DEMO_PASSWORD = "demo-password"
DEMO_ORG = "Northwind Design Studio"

# Resetting twice in a row (a double click, or several visitors at once) only rebuilds once.
RESET_COOLDOWN = timedelta(seconds=30)


def month_start(months_ago: int) -> date:
    today = date.today()
    year, month = today.year, today.month - months_ago
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


@dataclass(frozen=True)
class BankLine:
    day: date
    description: str
    amount: str  # as a bank CSV would show it


def _statement(first: date, second: date, third: date) -> list[BankLine]:
    """Three months of a small design studio's checking account."""
    lines = []
    for start in (first, second, third):
        d = start.replace
        lines += [
            BankLine(d(day=2), "ADOBE *CREATIVE CLOUD", "-59.99"),
            BankLine(d(day=3), "GITHUB INC", "-21.00"),
            BankLine(d(day=5), "COWORK SPACE RENT", "-850.00"),
            BankLine(d(day=6), "SQ *BLUE BOTTLE COFFEE #1204", "-6.75"),
            BankLine(d(day=9), "AWS EMEA", "-38.40"),
            BankLine(d(day=12), "COMCAST BUSINESS", "-89.99"),
            BankLine(d(day=14), "SQ *BLUE BOTTLE COFFEE #1188", "-5.50"),
            BankLine(d(day=18), "UBER TRIP 8837461", "-24.30"),
            BankLine(d(day=21), "MONTHLY SERVICE FEE", "-12.00"),
            BankLine(d(day=25), "FIGMA", "-45.00"),
        ]
    lines += [
        BankLine(first.replace(day=16), "DELTA AIR LINES", "-412.60"),
        BankLine(first.replace(day=27), "OWNER TRANSFER", "-1500.00"),
        BankLine(second.replace(day=11), "STAPLES 00234", "-64.18"),
        BankLine(second.replace(day=20), "LINKEDIN ADS", "-150.00"),
        BankLine(third.replace(day=8), "NOTION LABS", "-10.00"),
        BankLine(third.replace(day=15), "WEWORK DAY PASS", "-35.00"),
    ]
    return lines


def _as_csv_amount(cents: int) -> str:
    """12345 -> "123.45", with integer math (no floats)."""
    return f"{cents // 100}.{cents % 100:02d}"


def _account_ids(db: Session, org_id: int) -> dict[str, int]:
    return {a.code: a.id for a in db.scalars(select(Account).where(Account.org_id == org_id))}


def _populate(db: Session, user: User, org_id: int, storage: FileStorage) -> None:
    """Fill an empty organization with about three months of activity."""
    a = _account_ids(db, org_id)
    first, second, third = month_start(3), month_start(2), month_start(1)
    opening = first - timedelta(days=1)

    # Opening balance: the owner put money into the business.
    ledger_service.post_entry(
        db,
        org_id,
        user.id,
        opening,
        "Opening balance",
        [LineInput(a["1000"], 12_000_00), LineInput(a["3000"], -12_000_00)],
    )

    # Rules the owner set up.
    for pattern, code, field in [
        ("github", "5500", MatchField.DESCRIPTION),
        ("aws", "5500", MatchField.DESCRIPTION),
        ("adobe", "5500", MatchField.DESCRIPTION),
        ("figma", "5500", MatchField.DESCRIPTION),
        ("cowork space rent", "5400", MatchField.DESCRIPTION),
        ("service fee", "5100", MatchField.DESCRIPTION),
        ("comcast", "5700", MatchField.DESCRIPTION),
    ]:
        categorization_service.create_rule(
            db, org_id, pattern=pattern, account_id=a[code], match_field=field
        )

    # Customers and invoices: paid, partly paid, overdue, current and a draft.
    customers = {
        name: invoice_service.create_customer(db, org_id, name, email).id
        for name, email in [
            ("Harbor Coffee Roasters", "ap@harborcoffee.example"),
            ("Lumen Health", "billing@lumenhealth.example"),
            ("Oakridge Architects", None),
        ]
    }

    def invoice(customer: str, issued: date, due_days: int, lines: list[tuple[str, str, int]]):
        return invoice_service.create_invoice(
            db,
            org_id,
            customers[customer],
            issued,
            issued + timedelta(days=due_days),
            "",
            [LineData(desc, Decimal(qty), price, a["4000"]) for desc, qty, price in lines],
        )

    brand = invoice(
        "Harbor Coffee Roasters",
        first.replace(day=4),
        30,
        [("Brand identity package", "1", 4_800_00), ("Logo revisions (hours)", "6", 95_00)],
    )
    web = invoice(
        "Lumen Health", second.replace(day=2), 30, [("Website design (hours)", "42.5", 110_00)]
    )
    overdue = invoice(
        "Oakridge Architects", second.replace(day=15), 14, [("Portfolio site", "1", 2_200_00)]
    )
    current = invoice(
        "Harbor Coffee Roasters",
        third.replace(day=10),
        45,
        [("Packaging design", "1", 3_150_00), ("Print production (hours)", "8", 95_00)],
    )
    invoice("Lumen Health", date.today(), 30, [("Landing page refresh", "1", 1_600_00)])
    for inv in (brand, web, overdue, current):
        invoice_service.send_invoice(db, org_id, user.id, inv.id)

    brand_paid = brand.issue_date + timedelta(days=26)
    web_part = web.issue_date + timedelta(days=24)
    invoice_service.record_payment(db, org_id, user.id, brand.id, brand.total_cents, brand_paid)
    invoice_service.record_payment(db, org_id, user.id, web.id, 3_000_00, web_part)

    # The bank statement, including the two customer deposits.
    statement = _statement(first, second, third) + [
        BankLine(
            brand_paid + timedelta(days=1),
            "DEPOSIT HARBOR COFFEE",
            _as_csv_amount(brand.total_cents),
        ),
        BankLine(web_part + timedelta(days=1), "DEPOSIT LUMEN HEALTH", "3000.00"),
    ]
    statement.sort(key=lambda line: line.day)
    csv_text = "Date,Description,Amount\n" + "".join(
        f'{line.day.isoformat()},"{line.description}",{line.amount}\n' for line in statement
    )
    import_service.import_csv(
        db,
        storage,
        org_id,
        user.id,
        a["1000"],
        "demo-statement.csv",
        csv_text.encode(),
        ColumnMapping(date="Date", description="Description", amount="Amount"),
    )

    # Match the deposits to the payments recorded on the invoices.
    for tx in db.scalars(
        select(BankTransaction).where(
            BankTransaction.org_id == org_id, BankTransaction.description.like("DEPOSIT %")
        )
    ).all():
        candidate = reconciliation_service.match_candidates(db, org_id, tx.id)[0]
        reconciliation_service.match(db, org_id, tx.id, candidate.entry.id)

    # Rules suggest what they can; accept those, and hand-categorize most of the rest.
    for suggestion in categorization_service.suggest(db, org_id, NoLLMSuggester()):
        categorization_service.accept_suggestion(db, org_id, user.id, suggestion.id, False)
    by_hand = {
        "blue bottle coffee": "5200",
        "delta air lines": "5600",
        "owner transfer": "3100",
        "staples": "5300",
        "linkedin ads": "5000",
    }
    in_review = db.scalars(
        select(BankTransaction).where(
            BankTransaction.org_id == org_id,
            BankTransaction.status == BankTransactionStatus.FOR_REVIEW,
        )
    ).all()
    for tx in in_review:
        code = by_hand.get(tx.normalized_vendor)
        if code:
            categorization_service.categorize(db, org_id, user.id, tx.id, a[code])
    # Uber, Notion and the WeWork day pass are left in the review queue on purpose.


def is_demo_user(settings: Settings, user: User) -> bool:
    """True for the demo account, and only when the demo features are switched on."""
    return settings.demo_reset_enabled and user.email == DEMO_EMAIL


def seed_demo(db: Session, storage: FileStorage) -> bool:
    """Create the demo user and business. Returns False if the demo user already exists."""
    if db.scalars(select(User).where(User.email == DEMO_EMAIL)).first():
        logger.info("seed_skipped reason=demo_user_exists email=%s", DEMO_EMAIL)
        return False
    user = auth_service.signup(db, DEMO_EMAIL, DEMO_PASSWORD, DEMO_ORG)
    _populate(db, user, user.memberships[0].org_id, storage)
    logger.info("seed_done email=%s org=%s", DEMO_EMAIL, DEMO_ORG)
    return True


def reset_demo(db: Session, storage: FileStorage, user: User) -> Membership:
    """Delete every organization the demo user belongs to and rebuild a fresh demo business.

    One DELETE on organizations is enough: every table that holds an organization's data
    references it with ON DELETE CASCADE. If the demo business was rebuilt less than
    RESET_COOLDOWN ago, it is returned as it is.
    """
    memberships = org_service.list_memberships(db, user.id)
    if len(memberships) == 1:
        age = datetime.now(UTC) - memberships[0].organization.created_at
        if age < RESET_COOLDOWN:
            return memberships[0]

    org_ids = [m.org_id for m in memberships]
    db.execute(
        delete(Organization)
        .where(Organization.id.in_(org_ids))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()

    membership = org_service.create_organization(db, user, DEMO_ORG)
    _populate(db, user, membership.org_id, storage)
    logger.info("demo_reset user_id=%s org_id=%s", user.id, membership.org_id)
    return membership
