"""Reports against a known set of transactions, with every expected number worked out by hand.

The fixture (all amounts in dollars):
  Jan 01  Owner invests 10,000 into checking
  Jan 05  Rent 1,200 paid from checking
  Jan 10  Software 50 on the credit card
  Jan 15  Invoice 3,000 to a customer (AR / Sales)
  Jan 25  Customer pays 2,000 of it into checking
  Feb 03  Credit card bill 50 paid from checking
  Feb 10  Owner draws 500
  Feb 12  Travel 99.99 posted by mistake, then reversed the same day
  Feb 15  Cash sale 800 into checking
  Feb 20  Meals 45.50 from checking
  Mar 05  Sale 999 (outside the Jan-Feb reports)
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from tests.conftest import LoggedInUser, account_ids, post_entry

MakeUser = Callable[..., LoggedInUser]


@pytest.fixture
def books(client: TestClient, make_user: MakeUser) -> LoggedInUser:
    user = make_user("owner@example.com")
    a = account_ids(client, user)

    def entry(day: str, debit: str, credit: str, cents: int) -> dict:
        response = post_entry(client, user, [(a[debit], cents, 0), (a[credit], 0, cents)], day)
        assert response.status_code == 201, response.text
        return response.json()

    entry("2026-01-01", "1000", "3000", 1_000_000)
    entry("2026-01-05", "5400", "1000", 120_000)
    entry("2026-01-10", "5500", "2000", 5_000)
    entry("2026-01-15", "1100", "4000", 300_000)
    entry("2026-01-25", "1000", "1100", 200_000)
    entry("2026-02-03", "2000", "1000", 5_000)
    entry("2026-02-10", "3100", "1000", 50_000)
    mistake = entry("2026-02-12", "5600", "1000", 9_999)
    reversal = client.post(
        f"/api/v1/orgs/{user.org_id}/journal-entries/{mistake['id']}/reverse",
        headers=user.headers,
        json={"reversal_date": "2026-02-12"},
    )
    assert reversal.status_code == 201
    entry("2026-02-15", "1000", "4000", 80_000)
    entry("2026-02-20", "5200", "1000", 4_550)
    entry("2026-03-05", "1000", "4000", 99_900)
    return user


def get(client: TestClient, user: LoggedInUser, report: str, **params) -> dict:
    response = client.get(
        f"/api/v1/orgs/{user.org_id}/reports/{report}", params=params, headers=user.headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def lines(section: dict) -> dict[str, int]:
    return {line["code"]: line["amount_cents"] for line in section["lines"]}


# --- Profit and loss -----------------------------------------------------------------


def test_profit_and_loss_january(client: TestClient, books: LoggedInUser) -> None:
    pl = get(client, books, "profit-loss", date_from="2026-01-01", date_to="2026-01-31")

    assert lines(pl["income"]) == {"4000": 300_000}
    assert lines(pl["expenses"]) == {"5400": 120_000, "5500": 5_000}
    assert pl["net_income_cents"] == 175_000


def test_profit_and_loss_two_months_ignores_reversed_mistake(
    client: TestClient, books: LoggedInUser
) -> None:
    pl = get(client, books, "profit-loss", date_from="2026-01-01", date_to="2026-02-28")

    assert pl["income"]["total_cents"] == 380_000
    # Travel (5600) netted to zero after the reversal, so it isn't listed.
    assert lines(pl["expenses"]) == {"5200": 4_550, "5400": 120_000, "5500": 5_000}
    assert pl["net_income_cents"] == 250_450


# --- Balance sheet -------------------------------------------------------------------


def test_balance_sheet_end_of_february(client: TestClient, books: LoggedInUser) -> None:
    bs = get(client, books, "balance-sheet", as_of="2026-02-28")

    assert lines(bs["assets"]) == {"1000": 1_100_450, "1100": 100_000}
    assert bs["assets"]["total_cents"] == 1_200_450
    assert bs["liabilities"]["lines"] == []  # credit card paid off
    assert lines(bs["equity"]) == {"3000": 1_000_000, "3100": -50_000}
    assert bs["current_earnings_cents"] == 250_450
    assert bs["total_liabilities_and_equity_cents"] == 1_200_450
    assert bs["is_balanced"] is True


def test_balance_sheet_end_of_january(client: TestClient, books: LoggedInUser) -> None:
    bs = get(client, books, "balance-sheet", as_of="2026-01-31")

    assert bs["assets"]["total_cents"] == 1_180_000
    assert lines(bs["liabilities"]) == {"2000": 5_000}  # card balance still owed
    assert bs["current_earnings_cents"] == 175_000
    assert bs["total_equity_cents"] == 1_175_000
    assert bs["is_balanced"] is True


def test_balance_sheet_balances_on_every_day(client: TestClient, books: LoggedInUser) -> None:
    for as_of in ("2025-12-31", "2026-01-01", "2026-01-15", "2026-02-12", "2026-03-31"):
        assert get(client, books, "balance-sheet", as_of=as_of)["is_balanced"] is True, as_of


# --- Cash flow -----------------------------------------------------------------------


def test_cash_flow_two_months(client: TestClient, books: LoggedInUser) -> None:
    cf = get(client, books, "cash-flow", date_from="2026-01-01", date_to="2026-02-28")

    assert cf["opening_cash_cents"] == 0
    assert lines(cf["operating"]) == {
        "1100": 200_000,  # collected from the customer
        "4000": 80_000,  # cash sale
        "5200": -4_550,
        "5400": -120_000,
    }
    assert cf["operating"]["total_cents"] == 155_450
    assert lines(cf["financing"]) == {"2000": -5_000, "3000": 1_000_000, "3100": -50_000}
    assert cf["investing"]["total_cents"] == 0
    assert cf["net_change_cents"] == 1_100_450
    assert cf["closing_cash_cents"] == 1_100_450


def test_cash_flow_opening_plus_change_equals_closing(
    client: TestClient, books: LoggedInUser
) -> None:
    cf = get(client, books, "cash-flow", date_from="2026-02-01", date_to="2026-02-28")

    assert cf["opening_cash_cents"] == 1_080_000
    assert cf["operating"]["total_cents"] == 75_450
    assert cf["financing"]["total_cents"] == -55_000
    assert cf["opening_cash_cents"] + cf["net_change_cents"] == cf["closing_cash_cents"]
    assert cf["closing_cash_cents"] == 1_100_450


def test_cash_flow_ignores_non_cash_entries(client: TestClient, books: LoggedInUser) -> None:
    # January's credit-card software purchase and the invoice didn't move cash.
    cf = get(client, books, "cash-flow", date_from="2026-01-10", date_to="2026-01-15")

    assert cf["net_change_cents"] == 0
    assert cf["opening_cash_cents"] == cf["closing_cash_cents"] == 880_000


# --- Monthly totals ------------------------------------------------------------------


def test_monthly_income_and_expenses(client: TestClient, books: LoggedInUser) -> None:
    months = get(client, books, "monthly", date_from="2026-01-01", date_to="2026-03-31")

    assert [(m["month"], m["income_cents"], m["expense_cents"]) for m in months] == [
        ("2026-01-01", 300_000, 125_000),
        ("2026-02-01", 80_000, 4_550),
        ("2026-03-01", 99_900, 0),
    ]


def test_reversed_range_is_rejected(client: TestClient, books: LoggedInUser) -> None:
    response = client.get(
        f"/api/v1/orgs/{books.org_id}/reports/profit-loss",
        params={"date_from": "2026-02-01", "date_to": "2026-01-01"},
        headers=books.headers,
    )

    assert response.status_code == 422


def test_reports_only_include_own_organization(
    client: TestClient, books: LoggedInUser, make_user: MakeUser
) -> None:
    bob = make_user("bob@example.com")

    pl = get(client, bob, "profit-loss", date_from="2026-01-01", date_to="2026-12-31")
    bs = get(client, bob, "balance-sheet", as_of="2026-12-31")
    cf = get(client, bob, "cash-flow", date_from="2026-01-01", date_to="2026-12-31")

    assert pl["net_income_cents"] == 0 and pl["income"]["lines"] == []
    assert bs["assets"]["total_cents"] == 0
    assert cf["closing_cash_cents"] == 0
    other = client.get(f"/api/v1/orgs/{books.org_id}/reports/balance-sheet", headers=bob.headers)
    assert other.status_code == 404
