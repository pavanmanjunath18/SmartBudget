"""Double-entry rules: balance, immutability and reversals."""

import random
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JournalEntry, JournalLine
from app.models.journal import ImmutableLedgerError
from tests.conftest import LoggedInUser, account_ids, post_entry

MakeUser = Callable[..., LoggedInUser]


def _entry_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(JournalEntry))


# --- Balance invariant -----------------------------------------------------------


def test_balanced_entry_is_posted_with_signed_lines(
    client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)

    # Paid $49.99 for software from checking.
    response = post_entry(client, alice, [(ids["5500"], 4999, 0), (ids["1000"], 0, 4999)])

    assert response.status_code == 201
    body = response.json()
    assert [(line["debit_cents"], line["credit_cents"]) for line in body["lines"]] == [
        (4999, 0),
        (0, 4999),
    ]
    stored = db_session.scalars(
        select(JournalLine.amount_cents).where(JournalLine.entry_id == body["id"])
    ).all()
    assert sorted(stored) == [-4999, 4999]
    assert sum(stored) == 0


def test_unbalanced_entry_is_rejected_and_nothing_is_written(
    client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)

    response = post_entry(client, alice, [(ids["5500"], 5000, 0), (ids["1000"], 0, 4999)])

    assert response.status_code == 422
    assert "off by 1 cents" in response.json()["detail"]
    assert _entry_count(db_session) == 0


def test_split_entry_with_three_lines_is_allowed(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)

    # One $100 card charge split between meals and travel.
    response = post_entry(
        client,
        alice,
        [(ids["5200"], 3000, 0), (ids["5600"], 7000, 0), (ids["2000"], 0, 10000)],
    )

    assert response.status_code == 201


@pytest.mark.parametrize(
    "lines",
    [
        pytest.param([{"debit_cents": 100, "credit_cents": 100}], id="both-sides"),
        pytest.param([{"debit_cents": 0, "credit_cents": 0}], id="neither-side"),
        pytest.param([{"debit_cents": 12.5}], id="float-amount"),
        pytest.param([{"debit_cents": "100"}], id="string-amount"),
        pytest.param([{"debit_cents": -100}], id="negative-amount"),
    ],
)
def test_malformed_lines_are_rejected(
    client: TestClient, make_user: MakeUser, lines: list[dict]
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    bad = [{"account_id": ids["5500"], **line} for line in lines]
    good = {"account_id": ids["1000"], "credit_cents": 100}

    response = client.post(
        f"/api/v1/orgs/{alice.org_id}/journal-entries",
        headers=alice.headers,
        json={"entry_date": "2026-01-15", "lines": [*bad, good]},
    )

    assert response.status_code == 422


def test_single_line_entry_is_rejected(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)

    response = post_entry(client, alice, [(ids["5500"], 100, 0)])

    assert response.status_code == 422


def test_inactive_account_cannot_be_posted_to(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    client.patch(
        f"/api/v1/orgs/{alice.org_id}/accounts/{ids['5600']}",
        headers=alice.headers,
        json={"is_active": False},
    )

    response = post_entry(client, alice, [(ids["5600"], 100, 0), (ids["1000"], 0, 100)])

    assert response.status_code == 422
    assert "inactive" in response.json()["detail"]


def test_trial_balance_debits_equal_credits(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    post_entry(client, alice, [(ids["1000"], 500_000, 0), (ids["3000"], 0, 500_000)])
    post_entry(client, alice, [(ids["5400"], 120_000, 0), (ids["1000"], 0, 120_000)])
    post_entry(client, alice, [(ids["1100"], 250_000, 0), (ids["4000"], 0, 250_000)])

    tb = client.get(f"/api/v1/orgs/{alice.org_id}/trial-balance", headers=alice.headers).json()

    assert tb["total_debit_cents"] == tb["total_credit_cents"] == 750_000
    by_code = {line["code"]: line for line in tb["lines"]}
    assert by_code["1000"]["debit_cents"] == 380_000
    assert by_code["4000"]["credit_cents"] == 250_000


def test_ledger_stays_balanced_after_many_random_entries(
    client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    """Invariant check: whatever valid entries are posted, all lines sum to zero."""
    alice = make_user("alice@example.com")
    codes = list(account_ids(client, alice).items())
    rng = random.Random(42)  # fixed seed, so a failure is reproducible

    for _ in range(40):
        (_, debit_acct), (_, credit_acct) = rng.sample(codes, 2)
        amount = rng.randint(1, 1_000_000)
        post_entry(client, alice, [(debit_acct, amount, 0), (credit_acct, 0, amount)])

    total = db_session.scalar(
        select(func.sum(JournalLine.amount_cents)).where(JournalLine.org_id == alice.org_id)
    )
    assert total == 0
    tb = client.get(f"/api/v1/orgs/{alice.org_id}/trial-balance", headers=alice.headers).json()
    assert tb["total_debit_cents"] == tb["total_credit_cents"]


# --- Immutability ------------------------------------------------------------------


def test_there_is_no_route_to_edit_or_delete_an_entry(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    entry_id = post_entry(client, alice, [(ids["5500"], 100, 0), (ids["1000"], 0, 100)]).json()[
        "id"
    ]
    url = f"/api/v1/orgs/{alice.org_id}/journal-entries/{entry_id}"

    assert client.put(url, headers=alice.headers, json={}).status_code == 405
    assert client.patch(url, headers=alice.headers, json={}).status_code == 405
    assert client.delete(url, headers=alice.headers).status_code == 405


def test_orm_refuses_to_update_or_delete_posted_rows(
    client: TestClient, make_user: MakeUser, db_session: Session
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    entry_id = post_entry(client, alice, [(ids["5500"], 100, 0), (ids["1000"], 0, 100)]).json()[
        "id"
    ]
    line = db_session.scalars(select(JournalLine).where(JournalLine.entry_id == entry_id)).first()

    line.amount_cents = 999
    with pytest.raises(ImmutableLedgerError):
        db_session.flush()
    db_session.rollback()

    entry = db_session.get(JournalEntry, entry_id)
    db_session.delete(entry)
    with pytest.raises(ImmutableLedgerError):
        db_session.flush()


# --- Reversals ---------------------------------------------------------------------


def test_reversal_mirrors_the_entry_and_zeroes_the_balance(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    original = post_entry(
        client, alice, [(ids["5200"], 2500, 0), (ids["1000"], 0, 2500)], "2026-03-01"
    ).json()
    base = f"/api/v1/orgs/{alice.org_id}"

    reversal = client.post(
        f"{base}/journal-entries/{original['id']}/reverse",
        headers=alice.headers,
        json={"reversal_date": "2026-03-02"},
    )

    assert reversal.status_code == 201
    body = reversal.json()
    assert body["source"] == "reversal"
    assert body["reverses_entry_id"] == original["id"]
    assert [(line["debit_cents"], line["credit_cents"]) for line in body["lines"]] == [
        (0, 2500),
        (2500, 0),
    ]
    meals = client.get(f"{base}/accounts/{ids['5200']}/balance", headers=alice.headers).json()
    assert meals["balance_cents"] == 0
    # The original is still there, unchanged, and now links to its reversal.
    reloaded = client.get(f"{base}/journal-entries/{original['id']}", headers=alice.headers).json()
    assert reloaded["lines"] == original["lines"]
    assert reloaded["reversed_by_entry_id"] == body["id"]


def test_entry_can_only_be_reversed_once(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    entry_id = post_entry(client, alice, [(ids["5200"], 100, 0), (ids["1000"], 0, 100)]).json()[
        "id"
    ]
    url = f"/api/v1/orgs/{alice.org_id}/journal-entries/{entry_id}/reverse"

    first = client.post(url, headers=alice.headers)
    second = client.post(url, headers=alice.headers)

    assert first.status_code == 201
    assert second.status_code == 409


def test_a_reversal_cannot_itself_be_reversed(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    entry_id = post_entry(client, alice, [(ids["5200"], 100, 0), (ids["1000"], 0, 100)]).json()[
        "id"
    ]
    base = f"/api/v1/orgs/{alice.org_id}/journal-entries"
    reversal_id = client.post(f"{base}/{entry_id}/reverse", headers=alice.headers).json()["id"]

    response = client.post(f"{base}/{reversal_id}/reverse", headers=alice.headers)

    assert response.status_code == 422


def test_reversal_cannot_predate_the_original(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    entry_id = post_entry(
        client, alice, [(ids["5200"], 100, 0), (ids["1000"], 0, 100)], "2026-03-10"
    ).json()["id"]

    response = client.post(
        f"/api/v1/orgs/{alice.org_id}/journal-entries/{entry_id}/reverse",
        headers=alice.headers,
        json={"reversal_date": "2026-03-01"},
    )

    assert response.status_code == 422
