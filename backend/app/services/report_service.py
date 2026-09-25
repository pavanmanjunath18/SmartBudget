"""Financial reports computed from the ledger with SQL aggregation.

Every number here is a SUM over journal lines; nothing is stored or cached. Because
lines are signed (debit +, credit -), a raw sum is "debits minus credits". Reports
flip the sign for credit-normal accounts (income, liabilities, equity) so they read
as positive numbers.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.models import Account, AccountSubtype, AccountType, JournalEntry, JournalLine
from app.services.account_service import natural_balance


@dataclass(frozen=True)
class ReportLine:
    account_id: int
    code: str
    name: str
    amount_cents: int


@dataclass
class Section:
    lines: list[ReportLine] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return sum(line.amount_cents for line in self.lines)


def _sums_by_account(
    db: Session,
    org_id: int,
    types: list[AccountType],
    date_from: date | None,
    date_to: date,
) -> list[tuple[Account, int]]:
    """(account, debits - credits) for accounts of the given types, within the dates."""
    stmt = (
        select(Account, func.sum(JournalLine.amount_cents))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(
            Account.org_id == org_id,
            Account.type.in_(types),
            JournalEntry.entry_date <= date_to,
        )
        .group_by(Account.id)
        .order_by(Account.code)
    )
    if date_from is not None:
        stmt = stmt.where(JournalEntry.entry_date >= date_from)
    return [(account, int(raw)) for account, raw in db.execute(stmt).all()]


def _section(rows: list[tuple[Account, int]], account_type: AccountType) -> Section:
    """Lines of one account type in natural sign, leaving out accounts that net to 0."""
    section = Section()
    for account, raw in rows:
        if account.type == account_type and raw != 0:
            section.lines.append(
                ReportLine(account.id, account.code, account.name, natural_balance(account, raw))
            )
    return section


# --- Profit and loss -----------------------------------------------------------------


@dataclass
class ProfitAndLoss:
    date_from: date
    date_to: date
    income: Section
    expenses: Section

    @property
    def net_income_cents(self) -> int:
        return self.income.total_cents - self.expenses.total_cents


def profit_and_loss(db: Session, org_id: int, date_from: date, date_to: date) -> ProfitAndLoss:
    """Income minus expenses for entries dated within [date_from, date_to]."""
    rows = _sums_by_account(
        db, org_id, [AccountType.INCOME, AccountType.EXPENSE], date_from, date_to
    )
    return ProfitAndLoss(
        date_from=date_from,
        date_to=date_to,
        income=_section(rows, AccountType.INCOME),
        expenses=_section(rows, AccountType.EXPENSE),
    )


# --- Balance sheet -------------------------------------------------------------------


@dataclass
class BalanceSheet:
    as_of: date
    assets: Section
    liabilities: Section
    equity: Section
    current_earnings_cents: int

    @property
    def total_equity_cents(self) -> int:
        return self.equity.total_cents + self.current_earnings_cents

    @property
    def is_balanced(self) -> bool:
        return self.assets.total_cents == self.liabilities.total_cents + self.total_equity_cents


def balance_sheet(db: Session, org_id: int, as_of: date) -> BalanceSheet:
    """Assets, liabilities and equity on `as_of` (everything dated on or before it).

    There are no year-end closing entries, so profit earned so far still sits in the
    income and expense accounts. It is shown as "current earnings" in equity; without
    it, assets would not equal liabilities + equity.
    """
    rows = _sums_by_account(db, org_id, list(AccountType), None, as_of)
    income = _section(rows, AccountType.INCOME).total_cents
    expenses = _section(rows, AccountType.EXPENSE).total_cents
    return BalanceSheet(
        as_of=as_of,
        assets=_section(rows, AccountType.ASSET),
        liabilities=_section(rows, AccountType.LIABILITY),
        equity=_section(rows, AccountType.EQUITY),
        current_earnings_cents=income - expenses,
    )


# --- Cash flow summary ---------------------------------------------------------------


def _cash_flow_section(account: Account) -> str:
    """Simplified classification by the account on the other side of the cash movement."""
    if account.type in (AccountType.INCOME, AccountType.EXPENSE):
        return "operating"
    if account.subtype == AccountSubtype.ACCOUNTS_RECEIVABLE:
        return "operating"  # collecting from customers
    if account.type in (AccountType.LIABILITY, AccountType.EQUITY):
        return "financing"  # loans, credit card paydowns, owner money in and out
    return "investing"  # other assets, e.g. equipment


@dataclass
class CashFlow:
    date_from: date
    date_to: date
    opening_cash_cents: int
    closing_cash_cents: int
    operating: Section
    investing: Section
    financing: Section

    @property
    def net_change_cents(self) -> int:
        return self.operating.total_cents + self.investing.total_cents + self.financing.total_cents


def _cash_balance(db: Session, org_id: int, before_or_on: date) -> int:
    stmt = (
        select(func.coalesce(func.sum(JournalLine.amount_cents), 0))
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .join(Account, JournalLine.account_id == Account.id)
        .where(
            JournalLine.org_id == org_id,
            Account.subtype == AccountSubtype.BANK,
            JournalEntry.entry_date <= before_or_on,
        )
    )
    return int(db.scalar(stmt))


def cash_flow(db: Session, org_id: int, date_from: date, date_to: date) -> CashFlow:
    """Where the money in the bank came from and went to between two dates.

    For each entry that touches a bank account, its *other* lines explain the cash
    movement: because every entry sums to zero, the cash effect of each non-bank line
    is minus its amount. Those effects are grouped by account into operating,
    investing and financing. Transfers between two bank accounts have no non-bank
    lines, so they correctly net to nothing.
    """
    bank_account_ids = select(Account.id).where(
        Account.org_id == org_id, Account.subtype == AccountSubtype.BANK
    )
    cash_entry_ids = (
        select(JournalLine.entry_id)
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .where(
            JournalLine.org_id == org_id,
            JournalLine.account_id.in_(bank_account_ids),
            JournalEntry.entry_date.between(date_from, date_to),
        )
    )
    stmt = (
        select(Account, func.sum(-JournalLine.amount_cents))
        .join(JournalLine, JournalLine.account_id == Account.id)
        .where(
            JournalLine.entry_id.in_(cash_entry_ids),
            JournalLine.account_id.not_in(bank_account_ids),
        )
        .group_by(Account.id)
        .order_by(Account.code)
    )
    sections = {"operating": Section(), "investing": Section(), "financing": Section()}
    for account, cash_effect in db.execute(stmt).all():
        if cash_effect:
            sections[_cash_flow_section(account)].lines.append(
                ReportLine(account.id, account.code, account.name, int(cash_effect))
            )
    return CashFlow(
        date_from=date_from,
        date_to=date_to,
        opening_cash_cents=_cash_balance(db, org_id, date_from - timedelta(days=1)),
        closing_cash_cents=_cash_balance(db, org_id, date_to),
        **sections,
    )


# --- Monthly income vs expenses (dashboard chart) ------------------------------------


@dataclass(frozen=True)
class MonthTotals:
    month: date  # first day of the month
    income_cents: int
    expense_cents: int


def monthly_income_expense(
    db: Session, org_id: int, date_from: date, date_to: date
) -> list[MonthTotals]:
    """Income and expense totals per calendar month, one SQL query."""
    month = cast(func.date_trunc("month", JournalEntry.entry_date), Date)
    stmt = (
        select(month, Account.type, func.sum(JournalLine.amount_cents))
        .join(JournalEntry, JournalLine.entry_id == JournalEntry.id)
        .join(Account, JournalLine.account_id == Account.id)
        .where(
            JournalLine.org_id == org_id,
            Account.type.in_([AccountType.INCOME, AccountType.EXPENSE]),
            JournalEntry.entry_date.between(date_from, date_to),
        )
        .group_by(month, Account.type)
        .order_by(month)
    )
    totals: dict[date, dict[str, int]] = {}
    for month_start, account_type, raw in db.execute(stmt).all():
        totals.setdefault(month_start, {"income": 0, "expense": 0})
        # Income is credit-normal (raw sum negative), expenses debit-normal.
        if account_type == AccountType.INCOME:
            totals[month_start]["income"] = -int(raw)
        else:
            totals[month_start]["expense"] = int(raw)
    return [MonthTotals(m, t["income"], t["expense"]) for m, t in sorted(totals.items())]
