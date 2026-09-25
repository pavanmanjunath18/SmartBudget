"""CSV import through the API: validation report, idempotent dedupe, history."""

import json
from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.conftest import InMemoryStorage, LoggedInUser, account_ids

MakeUser = Callable[..., LoggedInUser]

STATEMENT = """Date,Description,Amount
2026-01-03,SQ *BLUE BOTTLE #123,-4.50
2026-01-03,SQ *BLUE BOTTLE #123,-4.50
2026-01-04,GITHUB INC,-21.00
2026-01-05,Client payment ACME,"1,500.00"
"""

MAPPING = {"date": "Date", "description": "Description", "amount": "Amount"}


def upload(
    client: TestClient,
    user: LoggedInUser,
    csv_text: str,
    account_id: int,
    mapping: dict | None = None,
    filename: str = "jan.csv",
):
    return client.post(
        f"/api/v1/orgs/{user.org_id}/imports",
        headers=user.headers,
        files={"file": (filename, csv_text.encode(), "text/csv")},
        data={"account_id": str(account_id), "mapping": json.dumps(mapping or MAPPING)},
    )


def bank_transactions(client: TestClient, user: LoggedInUser) -> list[dict]:
    return client.get(f"/api/v1/orgs/{user.org_id}/bank-transactions", headers=user.headers).json()


def test_import_creates_transactions_for_review(
    client: TestClient, make_user: MakeUser, storage: InMemoryStorage
) -> None:
    alice = make_user("alice@example.com")
    checking = account_ids(client, alice)["1000"]

    response = upload(client, alice, STATEMENT, checking)

    assert response.status_code == 201
    history = response.json()
    assert (history["total_rows"], history["imported_count"], history["duplicate_count"]) == (
        4,
        4,
        0,
    )
    rows = bank_transactions(client, alice)
    assert len(rows) == 4
    assert {r["status"] for r in rows} == {"for_review"}
    assert sorted(r["amount_cents"] for r in rows) == [-2100, -450, -450, 150000]
    assert {r["normalized_vendor"] for r in rows} >= {"blue bottle", "github inc"}
    # The raw file was kept through the storage interface.
    assert list(storage.files) == [f"org-{alice.org_id}/imports/{history['file_sha256']}.csv"]


def test_reimporting_the_same_file_creates_nothing(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    checking = account_ids(client, alice)["1000"]
    upload(client, alice, STATEMENT, checking)

    second = upload(client, alice, STATEMENT, checking).json()

    assert (second["imported_count"], second["duplicate_count"]) == (0, 4)
    assert len(bank_transactions(client, alice)) == 4


def test_overlapping_statements_only_add_new_rows(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    checking = account_ids(client, alice)["1000"]
    upload(client, alice, STATEMENT, checking)
    # The next export repeats Jan 4-5 and adds Jan 6. Different case/spacing too.
    later = """Date,Description,Amount
2026-01-04,github   inc,-21.00
2026-01-05,Client payment ACME,1500.00
2026-01-06,ZOOM.US,-15.99
"""

    result = upload(client, alice, later, checking, filename="jan-later.csv").json()

    assert (result["imported_count"], result["duplicate_count"]) == (1, 2)
    assert len(bank_transactions(client, alice)) == 5


def test_invalid_rows_are_reported_and_valid_rows_still_import(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    checking = account_ids(client, alice)["1000"]
    messy = """Date,Description,Amount
2026-01-03,Coffee,-4.50
2026-02-30,Bad date,-1.00
2026-01-04,,-2.00
2026-01-05,Too precise,-1.005
"""

    history = upload(client, alice, messy, checking).json()

    assert (history["imported_count"], history["invalid_count"]) == (1, 3)
    errors = {e["row"]: e["errors"] for e in history["errors"]}
    assert set(errors) == {3, 4, 5}
    assert "Invalid date" in errors[3][0]
    assert errors[4] == ["Description is empty"]
    assert "more than 2 decimal places" in errors[5][0]


def test_import_history_is_listed_and_readable(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    checking = account_ids(client, alice)["1000"]
    first = upload(client, alice, STATEMENT, checking, filename="a.csv").json()
    upload(client, alice, STATEMENT, checking, filename="b.csv")
    base = f"/api/v1/orgs/{alice.org_id}/imports"

    listed = client.get(base, headers=alice.headers).json()
    detail = client.get(f"{base}/{first['id']}", headers=alice.headers).json()

    assert [h["filename"] for h in listed] == ["b.csv", "a.csv"]
    assert detail["imported_count"] == 4


def test_debit_credit_mapping(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    checking = account_ids(client, alice)["1000"]
    csv_text = """Posted,Payee,Withdrawal,Deposit
01/31/2026,Rent,1200.00,
02/01/2026,Client,,800.00
"""
    mapping = {"date": "Posted", "description": "Payee", "debit": "Withdrawal", "credit": "Deposit"}

    history = upload(client, alice, csv_text, checking, mapping=mapping).json()

    assert history["imported_count"] == 2
    amounts = sorted(r["amount_cents"] for r in bank_transactions(client, alice))
    assert amounts == [-120000, 80000]


def test_missing_mapped_column_is_rejected(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    checking = account_ids(client, alice)["1000"]

    response = upload(client, alice, STATEMENT, checking, mapping={**MAPPING, "amount": "Value"})

    assert response.status_code == 422
    assert "not found" in response.json()["detail"]


def test_can_only_import_into_a_bank_account(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    sales = account_ids(client, alice)["4000"]

    response = upload(client, alice, STATEMENT, sales)

    assert response.status_code == 422


def test_preview_suggests_mapping(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")

    response = client.post(
        f"/api/v1/orgs/{alice.org_id}/imports/preview",
        headers=alice.headers,
        files={"file": ("jan.csv", STATEMENT.encode(), "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["headers"] == ["Date", "Description", "Amount"]
    assert body["suggested_mapping"] == MAPPING
    assert len(body["sample_rows"]) == 4


def test_filter_bank_transactions_by_status(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    upload(client, alice, STATEMENT, account_ids(client, alice)["1000"])
    base = f"/api/v1/orgs/{alice.org_id}/bank-transactions"

    for_review = client.get(base, params={"status": "for_review"}, headers=alice.headers).json()
    posted = client.get(base, params={"status": "posted"}, headers=alice.headers).json()

    assert len(for_review) == 4
    assert posted == []
