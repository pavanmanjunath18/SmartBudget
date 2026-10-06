"""One organization must never see another organization's data.

These tests grow with every stage: each new org-scoped resource gets a
"user B cannot read or change user A's X" test here.
"""

from collections.abc import Callable

from fastapi.testclient import TestClient

from tests.conftest import LoggedInUser, account_ids, import_csv, post_entry

MakeUser = Callable[..., LoggedInUser]


def test_member_can_read_own_org(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com", org_name="Alice LLC")

    response = client.get(f"/api/v1/orgs/{alice.org_id}", headers=alice.headers)

    assert response.status_code == 200
    assert response.json()["name"] == "Alice LLC"


def test_non_member_gets_404_for_someone_elses_org(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com", org_name="Alice LLC")
    bob = make_user("bob@example.com", org_name="Bob Co")

    response = client.get(f"/api/v1/orgs/{alice.org_id}", headers=bob.headers)

    assert response.status_code == 404


def test_other_org_and_missing_org_look_identical(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")

    other = client.get(f"/api/v1/orgs/{alice.org_id}", headers=bob.headers)
    missing = client.get("/api/v1/orgs/999999", headers=bob.headers)

    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


def test_org_list_only_contains_own_orgs(client: TestClient, make_user: MakeUser) -> None:
    make_user("alice@example.com", org_name="Alice LLC")
    bob = make_user("bob@example.com", org_name="Bob Co")

    names = [o["name"] for o in client.get("/api/v1/orgs", headers=bob.headers).json()]

    assert names == ["Bob Co"]


def test_user_can_create_and_access_a_second_org(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com", org_name="Alice LLC")

    created = client.post("/api/v1/orgs", json={"name": "Side Project"}, headers=alice.headers)

    assert created.status_code == 201
    assert created.json()["role"] == "owner"
    second_id = created.json()["id"]
    assert client.get(f"/api/v1/orgs/{second_id}", headers=alice.headers).status_code == 200
    names = [o["name"] for o in client.get("/api/v1/orgs", headers=alice.headers).json()]
    assert names == ["Alice LLC", "Side Project"]


def test_org_routes_require_login(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")

    assert client.get("/api/v1/orgs").status_code == 401
    assert client.get(f"/api/v1/orgs/{alice.org_id}").status_code == 401


def test_cannot_list_or_read_another_orgs_accounts_or_entries(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    alice_ids = account_ids(client, alice)
    entry_id = post_entry(
        client, alice, [(alice_ids["5500"], 100, 0), (alice_ids["1000"], 0, 100)]
    ).json()["id"]
    alice_base = f"/api/v1/orgs/{alice.org_id}"

    for path in (
        "/accounts",
        f"/accounts/{alice_ids['1000']}/balance",
        "/journal-entries",
        f"/journal-entries/{entry_id}",
        "/trial-balance",
    ):
        assert client.get(alice_base + path, headers=bob.headers).status_code == 404, path


def test_cannot_reach_another_orgs_rows_through_own_org_url(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    alice_ids = account_ids(client, alice)
    entry_id = post_entry(
        client, alice, [(alice_ids["5500"], 100, 0), (alice_ids["1000"], 0, 100)]
    ).json()["id"]
    bob_base = f"/api/v1/orgs/{bob.org_id}"

    # Bob is a member of his own org, but Alice's ids must not resolve inside it.
    assert (
        client.get(f"{bob_base}/journal-entries/{entry_id}", headers=bob.headers).status_code == 404
    )
    assert (
        client.post(
            f"{bob_base}/journal-entries/{entry_id}/reverse", headers=bob.headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"{bob_base}/accounts/{alice_ids['1000']}/balance", headers=bob.headers
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"{bob_base}/accounts/{alice_ids['1000']}", headers=bob.headers, json={"name": "Mine"}
        ).status_code
        == 404
    )


def test_cannot_post_to_another_orgs_account(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    alice_ids = account_ids(client, alice)
    bob_ids = account_ids(client, bob)

    response = post_entry(client, bob, [(bob_ids["5500"], 100, 0), (alice_ids["1000"], 0, 100)])

    assert response.status_code == 422
    assert "not found" in response.json()["detail"]
    alice_checking = client.get(
        f"/api/v1/orgs/{alice.org_id}/accounts/{alice_ids['1000']}/balance", headers=alice.headers
    ).json()
    assert alice_checking["balance_cents"] == 0


def test_imports_and_bank_transactions_are_isolated(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    alice_checking = account_ids(client, alice)["1000"]
    csv_text = "Date,Description,Amount\n2026-01-03,Coffee,-4.50\n"
    mapping = '{"date": "Date", "description": "Description", "amount": "Amount"}'
    import_id = client.post(
        f"/api/v1/orgs/{alice.org_id}/imports",
        headers=alice.headers,
        files={"file": ("a.csv", csv_text.encode(), "text/csv")},
        data={"account_id": str(alice_checking), "mapping": mapping},
    ).json()["id"]
    bob_base = f"/api/v1/orgs/{bob.org_id}"

    assert (
        client.get(f"/api/v1/orgs/{alice.org_id}/imports", headers=bob.headers).status_code == 404
    )
    assert client.get(f"{bob_base}/imports/{import_id}", headers=bob.headers).status_code == 404
    assert client.get(f"{bob_base}/bank-transactions", headers=bob.headers).json() == []
    # Bob can't import into Alice's bank account from his own org.
    response = client.post(
        f"{bob_base}/imports",
        headers=bob.headers,
        files={"file": ("a.csv", csv_text.encode(), "text/csv")},
        data={"account_id": str(alice_checking), "mapping": mapping},
    )
    assert response.status_code == 404


def test_rules_suggestions_and_categorizing_are_isolated(
    client: TestClient, make_user: MakeUser, suggester
) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    alice_ids = account_ids(client, alice)
    bob_ids = account_ids(client, bob)
    suggester.keyword_to_code = {"github": "5500"}
    import_csv(client, alice, [("2026-01-04", "GITHUB INC", "-21.00")])
    rule = client.post(
        f"/api/v1/orgs/{alice.org_id}/rules",
        headers=alice.headers,
        json={"pattern": "aws", "account_id": alice_ids["5500"]},
    ).json()
    suggestion = client.post(
        f"/api/v1/orgs/{alice.org_id}/bank-transactions/suggest", headers=alice.headers
    ).json()[0]
    bob_base = f"/api/v1/orgs/{bob.org_id}"

    assert client.get(f"{bob_base}/rules", headers=bob.headers).json() == []
    assert client.get(f"{bob_base}/suggestions", headers=bob.headers).json() == []
    assert client.delete(f"{bob_base}/rules/{rule['id']}", headers=bob.headers).status_code == 404
    accept = client.post(f"{bob_base}/suggestions/{suggestion['id']}/accept", headers=bob.headers)
    assert accept.status_code == 404
    categorize = client.post(
        f"{bob_base}/bank-transactions/{suggestion['bank_transaction_id']}/categorize",
        headers=bob.headers,
        json={"account_id": bob_ids["5500"]},
    )
    assert categorize.status_code == 404
    # Bob can't point his own rule at Alice's account either.
    bad_rule = client.post(
        f"{bob_base}/rules",
        headers=bob.headers,
        json={"pattern": "x", "account_id": alice_ids["5500"]},
    )
    assert bad_rule.status_code == 422


def test_customers_invoices_and_payments_are_isolated(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    alice_ids = account_ids(client, alice)
    bob_ids = account_ids(client, bob)
    alice_base = f"/api/v1/orgs/{alice.org_id}"
    bob_base = f"/api/v1/orgs/{bob.org_id}"
    customer = client.post(
        f"{alice_base}/customers", headers=alice.headers, json={"name": "Acme"}
    ).json()["id"]
    line = {"description": "x", "quantity": "1", "unit_price_cents": 100}
    invoice = client.post(
        f"{alice_base}/invoices",
        headers=alice.headers,
        json={
            "customer_id": customer,
            "issue_date": "2026-03-01",
            "due_date": "2026-03-31",
            "lines": [{**line, "income_account_id": alice_ids["4000"]}],
        },
    ).json()["id"]
    client.post(f"{alice_base}/invoices/{invoice}/send", headers=alice.headers)

    assert client.get(f"{bob_base}/customers", headers=bob.headers).json() == []
    assert client.get(f"{bob_base}/invoices", headers=bob.headers).json() == []
    assert client.get(f"{bob_base}/customers/{customer}", headers=bob.headers).status_code == 404
    assert client.get(f"{bob_base}/invoices/{invoice}", headers=bob.headers).status_code == 404
    paid = client.post(
        f"{bob_base}/invoices/{invoice}/payments",
        headers=bob.headers,
        json={"amount_cents": 100, "payment_date": "2026-03-05"},
    )
    assert paid.status_code == 404
    # Bob can't bill Alice's customer from his own org.
    stolen = client.post(
        f"{bob_base}/invoices",
        headers=bob.headers,
        json={
            "customer_id": customer,
            "issue_date": "2026-03-01",
            "due_date": "2026-03-31",
            "lines": [{**line, "income_account_id": bob_ids["4000"]}],
        },
    )
    assert stolen.status_code == 404
    aging = client.get(
        f"{bob_base}/reports/ar-aging", params={"as_of": "2026-06-01"}, headers=bob.headers
    ).json()
    assert aging["rows"] == []


def test_accept_all_only_touches_own_organization(
    client: TestClient, make_user: MakeUser, suggester
) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    suggester.keyword_to_code = {"github": "5500"}
    import_csv(client, alice, [("2026-01-04", "GITHUB INC", "-21.00")])
    client.post(f"/api/v1/orgs/{alice.org_id}/bank-transactions/suggest", headers=alice.headers)

    bobs = client.post(f"/api/v1/orgs/{bob.org_id}/suggestions/accept-all", headers=bob.headers)
    # Bob can't run it against Alice's organization either.
    theirs = client.post(f"/api/v1/orgs/{alice.org_id}/suggestions/accept-all", headers=bob.headers)

    assert bobs.json() == {"accepted": 0, "skipped": 0, "remaining": 0}
    assert theirs.status_code == 404
    pending = client.get(f"/api/v1/orgs/{alice.org_id}/suggestions", headers=alice.headers).json()
    assert len(pending) == 1
