from collections.abc import Callable

from fastapi.testclient import TestClient

from app.services.account_service import DEFAULT_ACCOUNTS
from tests.conftest import LoggedInUser, account_ids, post_entry

MakeUser = Callable[..., LoggedInUser]


def test_new_org_gets_default_chart_of_accounts(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")

    accounts = client.get(f"/api/v1/orgs/{alice.org_id}/accounts", headers=alice.headers).json()

    assert [a["code"] for a in accounts] == [code for code, *_ in DEFAULT_ACCOUNTS]
    by_code = {a["code"]: a for a in accounts}
    assert by_code["1000"]["subtype"] == "bank"
    assert by_code["1100"]["subtype"] == "accounts_receivable"
    assert {a["type"] for a in accounts} == {"asset", "liability", "equity", "income", "expense"}


def test_create_account_and_reject_duplicate_code(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    url = f"/api/v1/orgs/{alice.org_id}/accounts"

    created = client.post(
        url, headers=alice.headers, json={"code": "5550", "name": "Hosting", "type": "expense"}
    )
    duplicate = client.post(
        url, headers=alice.headers, json={"code": "5550", "name": "Other", "type": "expense"}
    )

    assert created.status_code == 201
    assert created.json()["name"] == "Hosting"
    assert duplicate.status_code == 409


def test_same_code_is_fine_in_different_orgs(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    body = {"code": "5550", "name": "Hosting", "type": "expense"}

    a = client.post(f"/api/v1/orgs/{alice.org_id}/accounts", headers=alice.headers, json=body)
    b = client.post(f"/api/v1/orgs/{bob.org_id}/accounts", headers=bob.headers, json=body)

    assert a.status_code == b.status_code == 201


def test_account_type_cannot_be_changed(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)

    response = client.patch(
        f"/api/v1/orgs/{alice.org_id}/accounts/{ids['5200']}",
        headers=alice.headers,
        json={"name": "Meals & Coffee", "type": "income"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Meals & Coffee"
    assert response.json()["type"] == "expense"


def test_account_with_balance_cannot_be_deactivated(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    post_entry(client, alice, [(ids["5200"], 1500, 0), (ids["1000"], 0, 1500)])
    url = f"/api/v1/orgs/{alice.org_id}/accounts"

    with_balance = client.patch(
        f"{url}/{ids['5200']}", headers=alice.headers, json={"is_active": False}
    )
    unused = client.patch(f"{url}/{ids['5600']}", headers=alice.headers, json={"is_active": False})

    assert with_balance.status_code == 422
    assert unused.status_code == 200
    assert unused.json()["is_active"] is False


def test_balance_uses_natural_sign_and_as_of_date(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    # Jan: $1,000 of sales paid into checking. Feb: $200 more.
    post_entry(client, alice, [(ids["1000"], 100_000, 0), (ids["4000"], 0, 100_000)], "2026-01-10")
    post_entry(client, alice, [(ids["1000"], 20_000, 0), (ids["4000"], 0, 20_000)], "2026-02-10")
    url = f"/api/v1/orgs/{alice.org_id}/accounts"

    sales_all = client.get(f"{url}/{ids['4000']}/balance", headers=alice.headers).json()
    sales_jan = client.get(
        f"{url}/{ids['4000']}/balance", params={"as_of": "2026-01-31"}, headers=alice.headers
    ).json()
    checking = client.get(f"{url}/{ids['1000']}/balance", headers=alice.headers).json()

    # Income is credit-normal, but shows as a positive number.
    assert sales_all["balance_cents"] == 120_000
    assert sales_jan["balance_cents"] == 100_000
    assert checking["balance_cents"] == 120_000
