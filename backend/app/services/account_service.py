"""Chart of accounts: default accounts, create/update, and balances."""

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Account, AccountSubtype, AccountType, JournalEntry, JournalLine
from app.services.errors import BusinessRuleError, ConflictError, NotFoundError

logger = logging.getLogger(__name__)

# (code, name, type, subtype). Numbered by type, the usual bookkeeping convention:
# 1xxx assets, 2xxx liabilities, 3xxx equity, 4xxx income, 5xxx expenses.
DEFAULT_ACCOUNTS: list[tuple[str, str, AccountType, AccountSubtype | None]] = [
    ("1000", "Business Checking", AccountType.ASSET, AccountSubtype.BANK),
    ("1100", "Accounts Receivable", AccountType.ASSET, AccountSubtype.ACCOUNTS_RECEIVABLE),
    ("2000", "Credit Card", AccountType.LIABILITY, None),
    ("3000", "Owner's Equity", AccountType.EQUITY, None),
    ("3100", "Owner's Draw", AccountType.EQUITY, None),
    ("4000", "Sales", AccountType.INCOME, None),
    ("4900", "Other Income", AccountType.INCOME, None),
    ("5000", "Advertising & Marketing", AccountType.EXPENSE, None),
    ("5100", "Bank Fees", AccountType.EXPENSE, None),
    ("5200", "Meals", AccountType.EXPENSE, None),
    ("5300", "Office Supplies", AccountType.EXPENSE, None),
    ("5400", "Rent", AccountType.EXPENSE, None),
    ("5500", "Software & Subscriptions", AccountType.EXPENSE, None),
    ("5600", "Travel", AccountType.EXPENSE, None),
    ("5700", "Utilities", AccountType.EXPENSE, None),
    ("5800", "Professional Services", AccountType.EXPENSE, None),
    ("5900", "Other Expenses", AccountType.EXPENSE, None),
]


def seed_default_accounts(db: Session, org_id: int) -> None:
    """Give a new organization a starter chart of accounts. Does not commit."""
    db.add_all(
        Account(org_id=org_id, code=code, name=name, type=type_, subtype=subtype)
        for code, name, type_, subtype in DEFAULT_ACCOUNTS
    )
    db.flush()


def list_accounts(db: Session, org_id: int) -> list[Account]:
    """All accounts of the organization, ordered by code."""
    return list(db.scalars(select(Account).where(Account.org_id == org_id).order_by(Account.code)))


def get_account(db: Session, org_id: int, account_id: int) -> Account:
    """One account of this organization, or NotFoundError (also for other orgs' accounts)."""
    account = db.scalars(
        select(Account).where(Account.org_id == org_id, Account.id == account_id)
    ).first()
    if account is None:
        raise NotFoundError("Account not found.")
    return account


def create_account(db: Session, org_id: int, code: str, name: str, type_: AccountType) -> Account:
    """Add an account to the chart. Codes are unique within an organization."""
    account = Account(org_id=org_id, code=code.strip(), name=name.strip(), type=type_)
    db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(f"Account code {code} is already used.") from exc
    db.commit()
    logger.info("account_created org_id=%s account_id=%s", org_id, account.id)
    return account


def update_account(
    db: Session, org_id: int, account_id: int, name: str | None, is_active: bool | None
) -> Account:
    """Rename or (de)activate an account.

    The type can't change: that would silently move past activity between report
    sections. An account with a non-zero balance can't be deactivated, or its money
    would disappear from view.
    """
    account = get_account(db, org_id, account_id)
    if name is not None:
        account.name = name.strip()
    if is_active is False and account.is_active:
        if account_balance(db, org_id, account.id) != 0:
            raise BusinessRuleError("Can't deactivate an account that has a balance.")
        account.is_active = False
    elif is_active is True:
        account.is_active = True
    db.commit()
    return account


def account_balance(db: Session, org_id: int, account_id: int, as_of: date | None = None) -> int:
    """Raw ledger balance in cents: debits minus credits, up to and including `as_of`."""
    stmt = (
        select(func.coalesce(func.sum(JournalLine.amount_cents), 0))
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(JournalLine.org_id == org_id, JournalLine.account_id == account_id)
    )
    if as_of is not None:
        stmt = stmt.where(JournalEntry.entry_date <= as_of)
    return int(db.scalar(stmt))


def natural_balance(account: Account, raw_cents: int) -> int:
    """Show a balance the way people read it: positive when the account is "normal".

    Debits minus credits is positive for assets and expenses, but income, liabilities
    and equity grow with credits, so their raw balance is negative. Flipping the sign
    makes $500 of sales show as 500, not -500.
    """
    return raw_cents if account.is_debit_normal else -raw_cents


@dataclass(frozen=True)
class TrialBalanceRow:
    account: Account
    debit_cents: int
    credit_cents: int


def trial_balance(db: Session, org_id: int, as_of: date | None = None) -> list[TrialBalanceRow]:
    """Every account with activity, its balance in the debit or credit column.

    In a correct ledger the two columns always have equal totals.
    """
    balance = func.sum(JournalLine.amount_cents)
    stmt = (
        select(Account, balance)
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(Account.org_id == org_id)
        .group_by(Account.id)
        .order_by(Account.code)
    )
    if as_of is not None:
        stmt = stmt.where(JournalEntry.entry_date <= as_of)
    rows = []
    for account, raw in db.execute(stmt).all():
        raw = int(raw)
        rows.append(TrialBalanceRow(account, debit_cents=max(raw, 0), credit_cents=max(-raw, 0)))
    return rows
