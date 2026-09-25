"""Invoices and accounts receivable: drafts, sending, payments, overdue and aging."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.models import Invoice, InvoiceStatus, Payment
from app.services.invoice_service import display_status, line_amount_cents
from tests.conftest import LoggedInUser, account_ids

MakeUser = Callable[..., LoggedInUser]


def base(user: LoggedInUser) -> str:
    return f"/api/v1/orgs/{user.org_id}"


def balance(client: TestClient, user: LoggedInUser, account_id: int, as_of: str | None = None):
    params = {"as_of": as_of} if as_of else {}
    url = f"{base(user)}/accounts/{account_id}/balance"
    return client.get(url, params=params, headers=user.headers).json()["balance_cents"]


def make_customer(client: TestClient, user: LoggedInUser, name: str = "Acme Corp") -> int:
    response = client.post(
        f"{base(user)}/customers", headers=user.headers, json={"name": name, "email": None}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_invoice(
    client: TestClient,
    user: LoggedInUser,
    customer_id: int,
    lines: list[tuple[str, str, int, str]] | None = None,
    issue_date: str = "2026-03-01",
    # Far in the future so tests don't depend on today's date; overdue tests pass their own.
    due_date: str = "2099-12-31",
) -> dict:
    """lines: (description, quantity, unit_price_cents, income account code)."""
    ids = account_ids(client, user)
    lines = lines or [("Website design", "10", 15000, "4000")]
    response = client.post(
        f"{base(user)}/invoices",
        headers=user.headers,
        json={
            "customer_id": customer_id,
            "issue_date": issue_date,
            "due_date": due_date,
            "lines": [
                {
                    "description": d,
                    "quantity": q,
                    "unit_price_cents": p,
                    "income_account_id": ids[code],
                }
                for d, q, p, code in lines
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def send(client: TestClient, user: LoggedInUser, invoice_id: int):
    return client.post(f"{base(user)}/invoices/{invoice_id}/send", headers=user.headers)


def pay(client: TestClient, user: LoggedInUser, invoice_id: int, cents: int, on: str):
    return client.post(
        f"{base(user)}/invoices/{invoice_id}/payments",
        headers=user.headers,
        json={"amount_cents": cents, "payment_date": on},
    )


# --- Pure helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "unit_price", "expected"),
    [
        ("10", 15000, 150000),
        ("1.5", 3333, 5000),  # 4999.5 rounds half-up to 5000
        ("0.33", 100, 33),
        ("3", 1999, 5997),
        ("2.25", 1, 2),  # 2.25 cents -> 2
    ],
)
def test_line_amount_rounds_once_per_line(quantity: str, unit_price: int, expected: int) -> None:
    assert line_amount_cents(Decimal(quantity), unit_price) == expected


def _invoice(status: InvoiceStatus, due: date, total: int = 1000, paid: int = 0) -> Invoice:
    invoice = Invoice(status=status, due_date=due, total_cents=total)
    invoice.payments = [Payment(amount_cents=paid)] if paid else []
    return invoice


def test_overdue_is_computed_not_stored() -> None:
    today = date(2026, 4, 10)

    assert display_status(_invoice(InvoiceStatus.SENT, date(2026, 4, 9)), today) == "overdue"
    assert display_status(_invoice(InvoiceStatus.SENT, date(2026, 4, 10)), today) == "sent"
    assert display_status(_invoice(InvoiceStatus.DRAFT, date(2026, 1, 1)), today) == "draft"
    assert display_status(_invoice(InvoiceStatus.PAID, date(2026, 1, 1)), today) == "paid"


# --- Drafts -------------------------------------------------------------------------


def test_draft_gets_next_number_and_total_but_no_ledger_entry(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    customer = make_customer(client, alice)

    first = make_invoice(
        client, alice, customer, [("Design", "10", 15000, "4000"), ("Hosting", "1.5", 3333, "4000")]
    )
    second = make_invoice(client, alice, customer)

    assert (first["number"], second["number"]) == (1, 2)
    assert first["status"] == "draft"
    assert first["total_cents"] == 155000
    assert first["journal_entry_id"] is None
    tb = client.get(f"{base(alice)}/trial-balance", headers=alice.headers).json()
    assert tb["lines"] == []


def test_invoice_numbers_are_per_organization(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    make_invoice(client, alice, make_customer(client, alice))

    bobs_first = make_invoice(client, bob, make_customer(client, bob))

    assert bobs_first["number"] == 1


def test_draft_can_be_edited_and_deleted(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    invoice = make_invoice(client, alice, make_customer(client, alice))
    url = f"{base(alice)}/invoices/{invoice['id']}"

    edited = client.patch(
        url,
        headers=alice.headers,
        json={
            "memo": "Net 30",
            "lines": [
                {
                    "description": "Consulting",
                    "quantity": "2",
                    "unit_price_cents": 20000,
                    "income_account_id": ids["4000"],
                }
            ],
        },
    ).json()
    deleted = client.delete(url, headers=alice.headers)

    assert edited["memo"] == "Net 30"
    assert edited["total_cents"] == 40000
    assert [line["description"] for line in edited["lines"]] == ["Consulting"]
    assert deleted.status_code == 204
    assert client.get(url, headers=alice.headers).status_code == 404


def test_invoice_lines_must_use_income_accounts(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)

    response = client.post(
        f"{base(alice)}/invoices",
        headers=alice.headers,
        json={
            "customer_id": make_customer(client, alice),
            "issue_date": "2026-03-01",
            "due_date": "2026-03-31",
            "lines": [
                {
                    "description": "x",
                    "quantity": "1",
                    "unit_price_cents": 100,
                    "income_account_id": ids["5500"],
                }
            ],
        },
    )

    assert response.status_code == 422


def test_due_date_before_issue_date_is_rejected(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)

    response = client.post(
        f"{base(alice)}/invoices",
        headers=alice.headers,
        json={
            "customer_id": make_customer(client, alice),
            "issue_date": "2026-03-10",
            "due_date": "2026-03-01",
            "lines": [
                {
                    "description": "x",
                    "quantity": "1",
                    "unit_price_cents": 100,
                    "income_account_id": ids["4000"],
                }
            ],
        },
    )

    assert response.status_code == 422


# --- Sending ------------------------------------------------------------------------


def test_sending_posts_receivable_and_income(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    invoice = make_invoice(
        client,
        alice,
        make_customer(client, alice),
        [("Design", "10", 15000, "4000"), ("Workshop fee", "1", 50000, "4900")],
    )

    sent = send(client, alice, invoice["id"]).json()

    assert sent["status"] == "sent"
    assert balance(client, alice, ids["1100"]) == 200000  # accounts receivable
    assert balance(client, alice, ids["4000"]) == 150000
    assert balance(client, alice, ids["4900"]) == 50000
    entry = client.get(
        f"{base(alice)}/journal-entries/{sent['journal_entry_id']}", headers=alice.headers
    ).json()
    assert entry["source"] == "invoice"
    assert entry["entry_date"] == "2026-03-01"


def test_sent_invoice_cannot_be_edited_deleted_or_sent_again(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    invoice = make_invoice(client, alice, make_customer(client, alice))
    send(client, alice, invoice["id"])
    url = f"{base(alice)}/invoices/{invoice['id']}"

    assert client.patch(url, headers=alice.headers, json={"memo": "x"}).status_code == 422
    assert client.delete(url, headers=alice.headers).status_code == 422
    assert send(client, alice, invoice["id"]).status_code == 409


# --- Payments -----------------------------------------------------------------------


def test_partial_then_full_payment(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    invoice = make_invoice(client, alice, make_customer(client, alice))  # $1,500.00
    send(client, alice, invoice["id"])

    partial = pay(client, alice, invoice["id"], 50000, "2026-03-15").json()
    assert (partial["status"], partial["paid_cents"], partial["balance_due_cents"]) == (
        "sent",
        50000,
        100000,
    )
    assert balance(client, alice, ids["1100"]) == 100000

    full = pay(client, alice, invoice["id"], 100000, "2026-03-20").json()
    assert (full["status"], full["balance_due_cents"]) == ("paid", 0)
    assert balance(client, alice, ids["1100"]) == 0
    assert balance(client, alice, ids["1000"]) == 150000
    assert [p["amount_cents"] for p in full["payments"]] == [50000, 100000]


@pytest.mark.parametrize(
    ("setup", "amount"),
    [
        ("draft", 100),  # not sent yet
        ("sent", 150001),  # more than the balance due
        ("paid", 100),  # nothing left to pay
    ],
)
def test_invalid_payments_are_rejected(
    client: TestClient, make_user: MakeUser, setup: str, amount: int
) -> None:
    alice = make_user("alice@example.com")
    invoice = make_invoice(client, alice, make_customer(client, alice))
    if setup in ("sent", "paid"):
        send(client, alice, invoice["id"])
    if setup == "paid":
        pay(client, alice, invoice["id"], 150000, "2026-03-15")

    response = pay(client, alice, invoice["id"], amount, "2026-03-16")

    assert response.status_code == 422


def test_payment_cannot_predate_invoice(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    invoice = make_invoice(client, alice, make_customer(client, alice))
    send(client, alice, invoice["id"])

    assert pay(client, alice, invoice["id"], 100, "2026-02-01").status_code == 422


def test_overdue_filter_in_api(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    customer = make_customer(client, alice)
    old = make_invoice(client, alice, customer, issue_date="2026-01-01", due_date="2026-01-31")
    future = make_invoice(client, alice, customer, issue_date="2026-01-01", due_date="2099-12-31")
    send(client, alice, old["id"])
    send(client, alice, future["id"])

    overdue = client.get(
        f"{base(alice)}/invoices", params={"status": "overdue"}, headers=alice.headers
    ).json()

    assert [i["id"] for i in overdue] == [old["id"]]
    assert overdue[0]["status"] == "overdue"


# --- AR aging -----------------------------------------------------------------------


def test_ar_aging_buckets_and_ties_out_to_receivable(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    acme = make_customer(client, alice, "Acme Corp")
    globex = make_customer(client, alice, "Globex")

    def sent_invoice(customer: int, cents: int, issued: str, due: str) -> dict:
        invoice = make_invoice(client, alice, customer, [("Work", "1", cents, "4000")], issued, due)
        send(client, alice, invoice["id"])
        return invoice

    sent_invoice(acme, 10000, "2026-06-01", "2026-06-30")  # not yet due on Jun 15
    sent_invoice(acme, 20000, "2026-05-01", "2026-05-31")  # 15 days late
    sent_invoice(globex, 40000, "2026-03-01", "2026-03-31")  # 76 days late
    sent_invoice(globex, 80000, "2026-01-01", "2026-01-31")  # 135 days late
    paid_later = sent_invoice(globex, 5000, "2026-06-01", "2026-06-10")  # 5 days late
    pay(client, alice, paid_later["id"], 5000, "2026-06-20")  # paid after the report date
    make_invoice(client, alice, acme, [("Draft", "1", 99999, "4000")])  # drafts never count

    report = client.get(
        f"{base(alice)}/reports/ar-aging", params={"as_of": "2026-06-15"}, headers=alice.headers
    ).json()

    rows = {r["customer_name"]: r for r in report["rows"]}
    assert (rows["Acme Corp"]["current"], rows["Acme Corp"]["days_1_30"]) == (10000, 20000)
    assert rows["Globex"]["days_1_30"] == 5000  # still open on Jun 15
    assert rows["Globex"]["days_61_90"] == 40000
    assert rows["Globex"]["over_90"] == 80000
    assert report["totals"]["total_cents"] == 155000
    # The report and the ledger agree: total owed == AR account balance on that date.
    assert balance(client, alice, ids["1100"], as_of="2026-06-15") == 155000

    after = client.get(
        f"{base(alice)}/reports/ar-aging", params={"as_of": "2026-06-30"}, headers=alice.headers
    ).json()
    assert after["totals"]["total_cents"] == 150000
    assert balance(client, alice, ids["1100"], as_of="2026-06-30") == 150000
