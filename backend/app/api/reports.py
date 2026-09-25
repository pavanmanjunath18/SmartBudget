"""Profit & loss, balance sheet, cash flow summary and monthly totals."""

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context
from app.db.session import get_db
from app.schemas.report import (
    BalanceSheetRead,
    CashFlowRead,
    MonthTotalsRead,
    ProfitAndLossRead,
    ReportLineRead,
    SectionRead,
)
from app.services import report_service
from app.services.errors import BusinessRuleError
from app.services.report_service import Section

router = APIRouter(prefix="/orgs/{org_id}/reports", tags=["reports"])


def _section(section: Section) -> SectionRead:
    return SectionRead(
        lines=[ReportLineRead(**vars(line)) for line in section.lines],
        total_cents=section.total_cents,
    )


def _check_range(date_from: date, date_to: date) -> None:
    if date_from > date_to:
        raise BusinessRuleError("date_from must be on or before date_to.")


@router.get("/profit-loss", response_model=ProfitAndLossRead)
def profit_and_loss(
    date_from: date,
    date_to: date,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> ProfitAndLossRead:
    _check_range(date_from, date_to)
    report = report_service.profit_and_loss(db, ctx.org_id, date_from, date_to)
    return ProfitAndLossRead(
        date_from=report.date_from,
        date_to=report.date_to,
        income=_section(report.income),
        expenses=_section(report.expenses),
        net_income_cents=report.net_income_cents,
    )


@router.get("/balance-sheet", response_model=BalanceSheetRead)
def balance_sheet(
    as_of: date | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> BalanceSheetRead:
    report = report_service.balance_sheet(db, ctx.org_id, as_of or date.today())
    return BalanceSheetRead(
        as_of=report.as_of,
        assets=_section(report.assets),
        liabilities=_section(report.liabilities),
        equity=_section(report.equity),
        current_earnings_cents=report.current_earnings_cents,
        total_equity_cents=report.total_equity_cents,
        total_liabilities_and_equity_cents=report.liabilities.total_cents
        + report.total_equity_cents,
        is_balanced=report.is_balanced,
    )


@router.get("/cash-flow", response_model=CashFlowRead)
def cash_flow(
    date_from: date,
    date_to: date,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> CashFlowRead:
    _check_range(date_from, date_to)
    report = report_service.cash_flow(db, ctx.org_id, date_from, date_to)
    return CashFlowRead(
        date_from=report.date_from,
        date_to=report.date_to,
        opening_cash_cents=report.opening_cash_cents,
        operating=_section(report.operating),
        investing=_section(report.investing),
        financing=_section(report.financing),
        net_change_cents=report.net_change_cents,
        closing_cash_cents=report.closing_cash_cents,
    )


@router.get("/monthly", response_model=list[MonthTotalsRead])
def monthly(
    date_from: date,
    date_to: date,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> list[MonthTotalsRead]:
    """Income and expenses per month, for the dashboard chart."""
    _check_range(date_from, date_to)
    return [
        MonthTotalsRead(
            month=m.month,
            income_cents=m.income_cents,
            expense_cents=m.expense_cents,
            net_cents=m.income_cents - m.expense_cents,
        )
        for m in report_service.monthly_income_expense(db, ctx.org_id, date_from, date_to)
    ]
