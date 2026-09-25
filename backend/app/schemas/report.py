"""Response bodies for financial reports. All amounts are integer cents."""

from datetime import date

from pydantic import BaseModel


class ReportLineRead(BaseModel):
    account_id: int
    code: str
    name: str
    amount_cents: int


class SectionRead(BaseModel):
    lines: list[ReportLineRead]
    total_cents: int


class ProfitAndLossRead(BaseModel):
    date_from: date
    date_to: date
    income: SectionRead
    expenses: SectionRead
    net_income_cents: int


class BalanceSheetRead(BaseModel):
    as_of: date
    assets: SectionRead
    liabilities: SectionRead
    equity: SectionRead
    current_earnings_cents: int  # profit to date not yet closed into equity
    total_equity_cents: int
    total_liabilities_and_equity_cents: int
    is_balanced: bool


class CashFlowRead(BaseModel):
    date_from: date
    date_to: date
    opening_cash_cents: int
    operating: SectionRead
    investing: SectionRead
    financing: SectionRead
    net_change_cents: int
    closing_cash_cents: int


class MonthTotalsRead(BaseModel):
    month: date
    income_cents: int
    expense_cents: int
    net_cents: int
