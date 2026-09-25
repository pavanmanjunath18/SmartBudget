"""Matching bank lines to existing entries and reconciling against statement balances.

Scenario (dollars):
  Jan 01  Opening balance 5,000 (before any import, so never listed as uncleared)
  Jan 05  Invoice 1,500 sent to Acme
  Jan 20  Acme's payment recorded against the invoice (ledger: +1,500 to checking)
  Jan 28  Rent check 1,200 written (ledger: -1,200) - the bank hasn't cleared it in January
  January statement lines: Jan 10 GITHUB -21.00, Jan 15 BANK FEE -5.00, Jan 21 DEPOSIT ACME +1,500
  February statement line: Feb 02 CHECK 1001 -1,200 (the rent check clearing)
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JournalEntry
from tests.conftest import LoggedInUser, account_ids, import_csv, post_entry

MakeUser = Callable[..., LoggedInUser]

JANUARY_STATEMENT_BALANCE = 647_400  # 5,000 + 1,500 - 21 - 5
FEBRUARY_STATEMENT_BALANCE = 527_400  # after the 1,200 check clears


def base(user: LoggedInUser) -> str:
    return f"/api/v1/orgs/{user.org_id}"


@pytest.fixture
def books(client: TestClient, make_user: MakeUser) -> dict:
    user = make_user("owner@example.com")
    ids = account_ids(client, user)
    post_entry(client, user, [(ids["1000"], 500_000, 0), (ids["3000"], 0, 500_000)], "2026-01-01")
    customer = client.post(
        f"{base(user)}/customers", headers=user.headers, json={"name": "Acme"}
    ).json()["id"]
    invoice = client.post(
        f"{base(user)}/invoices",
        headers=user.headers,
        json={
            "customer_id": customer,
            "issue_date": "2026-01-05",
            "due_date": "2099-12-31",
            "lines": [
                {
                    "description": "Project",
                    "quantity": "1",
                    "unit_price_cents": 150_000,
                    "income_account_id": ids["4000"],
                }
            ],
        },
    ).json()["id"]
    client.post(f"{base(user)}/invoices/{invoice}/send", headers=user.headers)
    paid = client.post(
        f"{base(user)}/invoices/{invoice}/payments",
        headers=user.headers,
        json={"amount_cents": 150_000, "payment_date": "2026-01-20"},
    ).json()
    rent = post_entry(
        client, user, [(ids["5400"], 120_000, 0), (ids["1000"], 0, 120_000)], "2026-01-28"
    ).json()
    import_csv(
        client,
        user,
        [
            ("2026-01-10", "GITHUB", "-21.00"),
            ("2026-01-15", "BANK FEE", "-5.00"),
            ("2026-01-21", "DEPOSIT ACME", "1500.00"),
        ],
    )
    txs = {
        t["description"]: t["id"]
        for t in client.get(f"{base(user)}/bank-transactions", headers=user.headers).json()
    }
    return {
        "user": user,
        "ids": ids,
        "txs": txs,
        "payment_entry": paid["payments"][0]["journal_entry_id"],
        "rent_entry": rent["id"],
    }


def summary(client: TestClient, user: LoggedInUser, checking: int, day: str, balance: int):
    response = client.get(
        f"{base(user)}/reconciliation",
        headers=user.headers,
        params={
            "account_id": checking,
            "statement_date": day,
            "statement_balance_cents": balance,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def categorize(client: TestClient, user: LoggedInUser, tx_id: int, account_id: int) -> None:
    response = client.post(
        f"{base(user)}/bank-transactions/{tx_id}/categorize",
        headers=user.headers,
        json={"account_id": account_id},
    )
    assert response.status_code == 200, response.text


def match(client: TestClient, user: LoggedInUser, tx_id: int, entry_id: int):
    return client.post(
        f"{base(user)}/bank-transactions/{tx_id}/match",
        headers=user.headers,
        json={"journal_entry_id": entry_id},
    )


def test_match_candidates_find_the_recorded_payment(client: TestClient, books: dict) -> None:
    user, txs = books["user"], books["txs"]

    candidates = client.get(
        f"{base(user)}/bank-transactions/{txs['DEPOSIT ACME']}/match-candidates",
        headers=user.headers,
    ).json()

    assert [(c["journal_entry_id"], c["amount_cents"], c["source"]) for c in candidates] == [
        (books["payment_entry"], 150_000, "payment")
    ]


def test_matching_links_without_posting_twice(
    client: TestClient, books: dict, db_session: Session
) -> None:
    user, ids, txs = books["user"], books["ids"], books["txs"]
    entries_before = db_session.scalar(select(func.count()).select_from(JournalEntry))

    response = match(client, user, txs["DEPOSIT ACME"], books["payment_entry"])

    assert response.status_code == 200
    assert response.json()["status"] == "matched"
    assert response.json()["journal_entry_id"] == books["payment_entry"]
    assert db_session.scalar(select(func.count()).select_from(JournalEntry)) == entries_before
    checking = client.get(
        f"{base(user)}/accounts/{ids['1000']}/balance", headers=user.headers
    ).json()
    assert checking["balance_cents"] == 530_000  # the 1,500 is counted once


def test_an_entry_can_only_be_matched_once(client: TestClient, books: dict) -> None:
    user, txs = books["user"], books["txs"]
    match(client, user, txs["DEPOSIT ACME"], books["payment_entry"])
    import_csv(client, user, [("2026-01-22", "DEPOSIT ACME AGAIN", "1500.00")])
    second_tx = next(
        t["id"]
        for t in client.get(f"{base(user)}/bank-transactions", headers=user.headers).json()
        if t["description"] == "DEPOSIT ACME AGAIN"
    )

    candidates = client.get(
        f"{base(user)}/bank-transactions/{second_tx}/match-candidates", headers=user.headers
    ).json()
    response = match(client, user, second_tx, books["payment_entry"])

    assert candidates == []
    assert response.status_code == 409


def test_match_requires_same_account_and_amount(client: TestClient, books: dict) -> None:
    user, txs = books["user"], books["txs"]

    response = match(client, user, txs["GITHUB"], books["payment_entry"])

    assert response.status_code == 422


def test_unmatch_returns_the_line_to_review(client: TestClient, books: dict) -> None:
    user, txs = books["user"], books["txs"]
    match(client, user, txs["DEPOSIT ACME"], books["payment_entry"])

    response = client.post(
        f"{base(user)}/bank-transactions/{txs['DEPOSIT ACME']}/unmatch", headers=user.headers
    )

    assert response.json()["status"] == "for_review"
    assert response.json()["journal_entry_id"] is None


def test_summary_explains_the_difference(client: TestClient, books: dict) -> None:
    user, ids = books["user"], books["ids"]

    before = summary(client, user, ids["1000"], "2026-01-31", JANUARY_STATEMENT_BALANCE)

    assert before["ledger_balance_cents"] == 530_000  # 5,000 + 1,500 - 1,200
    assert before["difference_cents"] == 117_400
    assert [t["description"] for t in before["bank_only"]] == [
        "GITHUB",
        "BANK FEE",
        "DEPOSIT ACME",
    ]
    # The payment and the rent check are in the ledger but not yet tied to bank lines.
    # The opening balance is before the first import, so it isn't listed.
    assert {line["journal_entry_id"] for line in before["ledger_only"]} == {
        books["payment_entry"],
        books["rent_entry"],
    }
    assert before["period_start"] == "2026-01-10"


def test_full_month_end_workflow(client: TestClient, books: dict) -> None:
    user, ids, txs = books["user"], books["ids"], books["txs"]
    checking = ids["1000"]
    match(client, user, txs["DEPOSIT ACME"], books["payment_entry"])
    categorize(client, user, txs["GITHUB"], ids["5500"])
    categorize(client, user, txs["BANK FEE"], ids["5100"])

    january = summary(client, user, checking, "2026-01-31", JANUARY_STATEMENT_BALANCE)

    # Everything the bank showed is in the ledger. The only gap is the uncleared check.
    assert january["bank_only"] == []
    assert [(x["journal_entry_id"], x["amount_cents"]) for x in january["ledger_only"]] == [
        (books["rent_entry"], -120_000)
    ]
    assert january["difference_cents"] == 120_000
    refused = client.post(
        f"{base(user)}/reconciliation/complete",
        headers=user.headers,
        json={
            "account_id": checking,
            "statement_date": "2026-01-31",
            "statement_balance_cents": JANUARY_STATEMENT_BALANCE,
        },
    )
    assert refused.status_code == 422

    # February: the check clears; match it to the rent entry.
    import_csv(client, user, [("2026-02-02", "CHECK 1001", "-1200.00")])
    check_tx = next(
        t["id"]
        for t in client.get(f"{base(user)}/bank-transactions", headers=user.headers).json()
        if t["description"] == "CHECK 1001"
    )
    assert match(client, user, check_tx, books["rent_entry"]).status_code == 200
    february = summary(client, user, checking, "2026-02-28", FEBRUARY_STATEMENT_BALANCE)
    assert february["difference_cents"] == 0
    assert february["bank_only"] == [] and february["ledger_only"] == []
    assert february["ready_to_reconcile_count"] == 4

    done = client.post(
        f"{base(user)}/reconciliation/complete",
        headers=user.headers,
        json={
            "account_id": checking,
            "statement_date": "2026-02-28",
            "statement_balance_cents": FEBRUARY_STATEMENT_BALANCE,
        },
    )

    assert done.json() == {"reconciled_count": 4}
    after = summary(client, user, checking, "2026-02-28", FEBRUARY_STATEMENT_BALANCE)
    assert (after["already_reconciled_count"], after["ready_to_reconcile_count"]) == (4, 0)
    locked = client.post(f"{base(user)}/bank-transactions/{check_tx}/unmatch", headers=user.headers)
    assert locked.status_code == 422


def test_only_bank_accounts_can_be_reconciled(client: TestClient, books: dict) -> None:
    user, ids = books["user"], books["ids"]

    response = client.get(
        f"{base(user)}/reconciliation",
        headers=user.headers,
        params={
            "account_id": ids["4000"],
            "statement_date": "2026-01-31",
            "statement_balance_cents": 0,
        },
    )

    assert response.status_code == 422


def test_reconciliation_is_isolated_between_orgs(
    client: TestClient, books: dict, make_user: MakeUser
) -> None:
    alice_ids, txs = books["ids"], books["txs"]
    bob = make_user("bob@example.com")
    import_csv(client, bob, [("2026-01-21", "DEPOSIT", "1500.00")])
    bob_tx = client.get(f"{base(bob)}/bank-transactions", headers=bob.headers).json()[0]["id"]

    # Bob can't touch Alice's transaction, even through his own org URL.
    assert match(client, bob, txs["DEPOSIT ACME"], books["payment_entry"]).status_code == 404
    # Bob can't link his own bank line to Alice's ledger entry.
    assert match(client, bob, bob_tx, books["payment_entry"]).status_code == 422
    # Bob can't reconcile Alice's checking account.
    response = client.get(
        f"{base(bob)}/reconciliation",
        headers=bob.headers,
        params={
            "account_id": alice_ids["1000"],
            "statement_date": "2026-01-31",
            "statement_balance_cents": 0,
        },
    )
    assert response.status_code == 404
    candidates = client.get(
        f"{base(bob)}/bank-transactions/{bob_tx}/match-candidates", headers=bob.headers
    ).json()
    assert candidates == []
