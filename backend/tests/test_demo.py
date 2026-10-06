"""The live-demo experience: sample CSVs, what importing them does to the demo books, reset.

The seeded demo account has rules for GitHub, AWS, Adobe, Figma, the cowork rent, Comcast and
the monthly fee, and remembers Blue Bottle, Staples, LinkedIn Ads and Delta from earlier choices.
These tests import the sample files into that account through the real API.
"""

import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models import (
    Account,
    BankTransaction,
    CategoryRule,
    Customer,
    ImportHistory,
    Invoice,
    JournalEntry,
    JournalLine,
    VendorCategoryCache,
)
from app.services import demo_service, sample_data
from app.services.demo_service import DEMO_EMAIL, DEMO_PASSWORD
from tests.conftest import InMemoryStorage, LoggedInUser, account_ids

SIMPLE_MAPPING = {"date": "Date", "description": "Description", "amount": "Amount"}


@pytest.fixture
def demo_mode(client: TestClient) -> None:
    """Switch the demo features on for this test (they are off unless configured)."""
    enabled = get_settings().model_copy(update={"demo_reset_enabled": True})
    client.app.dependency_overrides[get_settings] = lambda: enabled


@pytest.fixture
def demo(client: TestClient, db_session: Session, demo_mode: None) -> LoggedInUser:
    """The seeded demo account, logged in."""
    demo_service.seed_demo(db_session, InMemoryStorage())
    login = client.post("/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    orgs = client.get("/api/v1/orgs", headers=headers).json()
    return LoggedInUser(email=DEMO_EMAIL, org_id=orgs[0]["id"], headers=headers)


def base(user: LoggedInUser) -> str:
    return f"/api/v1/orgs/{user.org_id}"


def upload_sample(client: TestClient, user: LoggedInUser, key: str, mapping=None) -> dict:
    csv_bytes = client.get(f"/api/v1/demo/samples/{key}.csv").content
    checking = account_ids(client, user)["1000"]
    response = client.post(
        f"{base(user)}/imports",
        headers=user.headers,
        files={"file": (f"{key}.csv", csv_bytes, "text/csv")},
        data={"account_id": str(checking), "mapping": json.dumps(mapping or SIMPLE_MAPPING)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def suggest(client: TestClient, user: LoggedInUser) -> list[dict]:
    response = client.post(f"{base(user)}/bank-transactions/suggest", headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()


def review_count(client: TestClient, user: LoggedInUser) -> int:
    url = f"{base(user)}/bank-transactions"
    return len(client.get(url, params={"status": "for_review"}, headers=user.headers).json())


def cash_cents(client: TestClient, user: LoggedInUser) -> int:
    url = f"{base(user)}/accounts/{account_ids(client, user)['1000']}/balance"
    return client.get(url, headers=user.headers).json()["balance_cents"]


# --- The sample files -----------------------------------------------------------------


def test_samples_are_listed_without_logging_in(client: TestClient) -> None:
    response = client.get("/api/v1/demo/samples")

    assert response.status_code == 200
    samples = response.json()
    assert [s["key"] for s in samples] == ["normal-month", "messy-export", "overlapping-export"]
    assert [s["row_count"] for s in samples] == [14, 17, 20]


def test_sample_download_is_a_csv_attachment(client: TestClient) -> None:
    response = client.get("/api/v1/demo/samples/normal-month.csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "smartbudget-sample-1-normal-month.csv" in response.headers["content-disposition"]
    assert response.text.splitlines()[0] == "Date,Description,Amount"


def test_unknown_sample_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/demo/samples/nope.csv").status_code == 404


@pytest.mark.parametrize("key", [s.key for s in sample_data.SAMPLES])
def test_sample_dates_are_within_the_last_month_and_never_in_the_future(key: str) -> None:
    today = date(2026, 3, 1)  # a short month and a month boundary on purpose

    text = sample_data.render(key, today).lstrip("﻿")
    dates = []
    for line in text.splitlines()[1:]:
        cell = line.split(",")[0]
        if cell[:2].isdigit() and "/" in cell:  # messy file: MM/DD/YYYY (bad rows may differ)
            month, day, year = cell.split("/")
            if int(month) > 12:
                continue
            dates.append(date(int(year), int(month), int(day)))
        elif cell.startswith("20"):
            dates.append(date.fromisoformat(cell))

    assert dates
    assert max(dates) < today
    assert min(dates) >= today - timedelta(days=30)


# --- Importing them into the seeded demo books ---------------------------------------


def test_normal_month_is_mostly_categorized_automatically(
    client: TestClient, demo: LoggedInUser
) -> None:
    history = upload_sample(client, demo, "normal-month")
    review_before = review_count(client, demo)

    suggestions = suggest(client, demo)

    assert (history["imported_count"], history["duplicate_count"], history["invalid_count"]) == (
        14,
        0,
        0,
    )
    by_source = {s: [x for x in suggestions if x["source"] == s] for s in ("rule", "cache", "llm")}
    assert (len(by_source["rule"]), len(by_source["cache"]), len(by_source["llm"])) == (7, 3, 0)
    # Uber, Notion, the Stripe payout and the WeWork pass have no rule and no history.
    assert review_before - len(suggestions) == 5 + 4


def test_accepting_all_suggestions_changes_the_books(
    client: TestClient, demo: LoggedInUser
) -> None:
    upload_sample(client, demo, "normal-month")
    cash_before = cash_cents(client, demo)
    suggestions = suggest(client, demo)

    result = client.post(f"{base(demo)}/suggestions/accept-all", headers=demo.headers)

    assert result.json() == {"accepted": 10, "skipped": 0, "remaining": 0}
    # All ten accepted rows are expenses, so cash falls by exactly their total.
    spent = sum(s["amount_cents"] for s in suggestions)
    assert spent < 0
    assert cash_cents(client, demo) == cash_before + spent
    # They are posted to the ledger now, so the dashboard's monthly totals include them.
    today = date.today()
    months = client.get(
        f"{base(demo)}/reports/monthly",
        params={
            "date_from": (today - timedelta(days=40)).isoformat(),
            "date_to": today.isoformat(),
        },
        headers=demo.headers,
    ).json()
    assert sum(m["expense_cents"] for m in months) >= -spent
    # Nothing left to accept.
    again = client.post(f"{base(demo)}/suggestions/accept-all", headers=demo.headers)
    assert again.json() == {"accepted": 0, "skipped": 0, "remaining": 0}


def test_messy_export_maps_columns_and_reports_each_bad_row(
    client: TestClient, demo: LoggedInUser
) -> None:
    csv_bytes = client.get("/api/v1/demo/samples/messy-export.csv").content
    preview = client.post(
        f"{base(demo)}/imports/preview",
        headers=demo.headers,
        files={"file": ("messy.csv", csv_bytes, "text/csv")},
    ).json()

    # The mapping guess understands this bank's column names (and ignores Balance).
    assert preview["suggested_mapping"] == {
        "date": "Posting Date",
        "description": "Payee",
        "debit": "Withdrawals",
        "credit": "Deposits",
    }
    history = upload_sample(client, demo, "messy-export", preview["suggested_mapping"])

    assert (history["imported_count"], history["invalid_count"]) == (12, 5)
    problems = " | ".join(" ".join(e["errors"]) for e in history["errors"])
    for expected in (
        "Invalid date",
        "Description is empty",
        "both a debit and a credit",
        "more than 2 decimal places",
        "no amount",
    ):
        assert expected in problems


def test_overlapping_export_skips_repeats_but_keeps_identical_coffees(
    client: TestClient, demo: LoggedInUser
) -> None:
    upload_sample(client, demo, "normal-month")

    overlap = upload_sample(client, demo, "overlapping-export")

    # 14 rows repeat file 1; the 6 new ones include two identical same-day coffees.
    assert (overlap["imported_count"], overlap["duplicate_count"]) == (6, 14)
    coffees = [
        t
        for t in client.get(f"{base(demo)}/bank-transactions", headers=demo.headers).json()
        if t["description"] == "SQ *BLUE BOTTLE COFFEE #2210" and t["amount_cents"] == -550
    ]
    assert len(coffees) == 2


def test_uploading_the_same_sample_twice_adds_nothing(
    client: TestClient, demo: LoggedInUser
) -> None:
    upload_sample(client, demo, "overlapping-export")

    again = upload_sample(client, demo, "overlapping-export")

    assert (again["imported_count"], again["duplicate_count"]) == (0, 20)


def test_all_three_samples_load_together_without_colliding(
    client: TestClient, demo: LoggedInUser
) -> None:
    first = upload_sample(client, demo, "normal-month")
    messy = upload_sample(
        client,
        demo,
        "messy-export",
        {
            "date": "Posting Date",
            "description": "Payee",
            "debit": "Withdrawals",
            "credit": "Deposits",
        },
    )
    overlap = upload_sample(client, demo, "overlapping-export")

    assert first["duplicate_count"] == 0  # never collides with the seeded statement
    assert messy["duplicate_count"] == 0
    assert (overlap["imported_count"], overlap["duplicate_count"]) == (6, 14)


# --- The demo flag and resetting ------------------------------------------------------


def test_me_flags_only_the_demo_account(client: TestClient, demo: LoggedInUser, make_user) -> None:
    other = make_user("someone@example.com")

    demo_me = client.get("/api/v1/auth/me", headers=demo.headers).json()
    other_me = client.get("/api/v1/auth/me", headers=other.headers).json()

    assert demo_me["demo"] is True
    assert other_me["demo"] is False


def test_reset_is_refused_when_demo_mode_is_off(client: TestClient, db_session: Session) -> None:
    demo_service.seed_demo(db_session, InMemoryStorage())  # no demo_mode fixture: flag is off
    login = client.post("/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert client.post("/api/v1/demo/reset", headers=headers).status_code == 403
    assert client.get("/api/v1/auth/me", headers=headers).json()["demo"] is False


def test_reset_is_refused_for_other_accounts(
    client: TestClient, demo_mode: None, make_user
) -> None:
    other = make_user("someone@example.com")

    response = client.post("/api/v1/demo/reset", headers=other.headers)

    assert response.status_code == 403


def test_reset_needs_login(client: TestClient, demo_mode: None) -> None:
    assert client.post("/api/v1/demo/reset").status_code == 401


def test_reset_rebuilds_the_demo_books(
    client: TestClient,
    demo: LoggedInUser,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo_service, "RESET_COOLDOWN", timedelta(0))
    upload_sample(client, demo, "normal-month")
    suggest(client, demo)
    client.post(f"{base(demo)}/suggestions/accept-all", headers=demo.headers)
    # Make the demo user own a second, stray organization too.
    client.post("/api/v1/orgs", headers=demo.headers, json={"name": "Scratch"})
    assert review_count(client, demo) == 9

    response = client.post("/api/v1/demo/reset", headers=demo.headers)

    assert response.status_code == 200
    new_org = response.json()
    assert new_org["name"] == demo_service.DEMO_ORG and new_org["id"] != demo.org_id
    # Only the rebuilt organization is left, and the old one is gone for good.
    orgs = client.get("/api/v1/orgs", headers=demo.headers).json()
    assert [o["id"] for o in orgs] == [new_org["id"]]
    assert client.get(f"/api/v1/orgs/{demo.org_id}", headers=demo.headers).status_code == 404
    # Nothing from the old organization is left behind in any table.
    for model in (
        Account,
        JournalEntry,
        JournalLine,
        BankTransaction,
        ImportHistory,
        CategoryRule,
        VendorCategoryCache,
        Customer,
        Invoice,
    ):
        left = db_session.scalar(
            select(func.count()).select_from(model).where(model.org_id == demo.org_id)
        )
        assert left == 0, model.__name__
    fresh = LoggedInUser(DEMO_EMAIL, new_org["id"], demo.headers)
    assert review_count(client, fresh) == 5  # exactly like a first-time seed
    balance_sheet = client.get(f"{base(fresh)}/reports/balance-sheet", headers=demo.headers)
    assert balance_sheet.json()["is_balanced"] is True


def test_reset_twice_in_a_row_only_rebuilds_once(client: TestClient, demo: LoggedInUser) -> None:
    # The seeded business is only seconds old, so it is inside the cooldown.
    response = client.post("/api/v1/demo/reset", headers=demo.headers)

    assert response.status_code == 200
    assert response.json()["id"] == demo.org_id


def test_resetting_the_demo_never_touches_other_organizations(
    client: TestClient, demo: LoggedInUser, make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_service, "RESET_COOLDOWN", timedelta(0))
    alice = make_user("alice@example.com", org_name="Alice LLC")
    alice_accounts_before = len(
        client.get(f"/api/v1/orgs/{alice.org_id}/accounts", headers=alice.headers).json()
    )

    client.post("/api/v1/demo/reset", headers=demo.headers)

    assert client.get(f"/api/v1/orgs/{alice.org_id}", headers=alice.headers).status_code == 200
    accounts = client.get(f"/api/v1/orgs/{alice.org_id}/accounts", headers=alice.headers).json()
    assert len(accounts) == alice_accounts_before


def test_settings_default_keeps_demo_features_off() -> None:
    assert Settings(_env_file=None, jwt_secret_key="0123456789abcdef").demo_reset_enabled is False
