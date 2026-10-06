"""Rules first, then the vendor cache, then the LLM; posting to the ledger on accept."""

from collections.abc import Callable
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from app.models import BankTransaction, CategoryRule, MatchField, MatchType
from app.services import categorization_service
from app.services.ai_service import (
    AccountOption,
    AnthropicSuggester,
    TransactionToCategorize,
    _Choice,
    _Choices,
)
from app.services.categorization_service import first_matching_rule, rule_matches
from tests.conftest import FakeSuggester, LoggedInUser, account_ids, import_csv

MakeUser = Callable[..., LoggedInUser]


def base(user: LoggedInUser) -> str:
    return f"/api/v1/orgs/{user.org_id}"


def suggest(client: TestClient, user: LoggedInUser) -> list[dict]:
    response = client.post(f"{base(user)}/bank-transactions/suggest", headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()


def balance(client: TestClient, user: LoggedInUser, account_id: int) -> int:
    url = f"{base(user)}/accounts/{account_id}/balance"
    return client.get(url, headers=user.headers).json()["balance_cents"]


def add_rule(client: TestClient, user: LoggedInUser, **body) -> dict:
    response = client.post(f"{base(user)}/rules", headers=user.headers, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- Rule matching (pure) -------------------------------------------------------------


def _tx(description: str, vendor: str = "") -> BankTransaction:
    return BankTransaction(description=description, normalized_vendor=vendor or description.lower())


def _rule(
    pattern: str,
    rule_id: int = 1,
    priority: int = 100,
    account_id: int = 1,
    is_active: bool = True,
    match_field: MatchField = MatchField.DESCRIPTION,
    match_type: MatchType = MatchType.CONTAINS,
) -> CategoryRule:
    return CategoryRule(
        id=rule_id,
        pattern=pattern,
        priority=priority,
        account_id=account_id,
        is_active=is_active,
        match_field=match_field,
        match_type=match_type,
    )


def test_rule_match_types_are_case_insensitive() -> None:
    tx = _tx("AWS EMEA Invoice 123", vendor="aws emea invoice")

    assert rule_matches(_rule("aws"), tx)
    assert rule_matches(_rule("aws emea", match_type=MatchType.STARTS_WITH), tx)
    assert not rule_matches(_rule("emea", match_type=MatchType.STARTS_WITH), tx)
    assert rule_matches(
        _rule("aws emea invoice", match_type=MatchType.EQUALS, match_field=MatchField.VENDOR), tx
    )
    assert not rule_matches(_rule("gcp"), tx)


def test_lowest_priority_number_wins_and_inactive_rules_are_skipped() -> None:
    tx = _tx("GITHUB SPONSORS")
    broad = _rule("github", rule_id=1, priority=50, account_id=10)
    specific = _rule("github sponsors", rule_id=2, priority=10, account_id=20)
    disabled = _rule("github", rule_id=3, priority=1, account_id=30, is_active=False)

    assert first_matching_rule([broad, specific, disabled], tx).account_id == 20


# --- Suggestion sources ---------------------------------------------------------------


def test_rules_are_used_before_the_llm(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    add_rule(client, alice, pattern="aws", account_id=ids["5500"])
    suggester.keyword_to_code = {"uber": "5600"}
    import_csv(
        client,
        alice,
        [("2026-01-03", "AWS EMEA", "-120.00"), ("2026-01-04", "UBER TRIP", "-18.40")],
    )

    suggestions = suggest(client, alice)

    by_source = {s["source"]: s for s in suggestions}
    assert by_source["rule"]["account_id"] == ids["5500"]
    assert by_source["llm"]["account_id"] == ids["5600"]
    # The LLM was only asked about the one transaction no rule matched.
    assert len(suggester.calls) == 1 and len(suggester.calls[0]) == 1


def test_confirmed_vendor_is_served_from_cache_without_llm(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    import_csv(client, alice, [("2026-01-03", "SQ *BLUE BOTTLE #12", "-4.50")])
    tx_id = client.get(f"{base(alice)}/bank-transactions", headers=alice.headers).json()[0]["id"]
    client.post(
        f"{base(alice)}/bank-transactions/{tx_id}/categorize",
        headers=alice.headers,
        json={"account_id": ids["5200"]},
    )
    import_csv(client, alice, [("2026-02-03", "SQ *BLUE BOTTLE #98", "-5.25")])

    suggestions = suggest(client, alice)

    assert [(s["source"], s["account_id"]) for s in suggestions] == [("cache", ids["5200"])]
    assert suggester.calls == []


def test_llm_answers_are_validated(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    bob = make_user("bob@example.com")
    ids = account_ids(client, alice)
    import_csv(
        client,
        alice,
        [
            ("2026-01-03", "Thing one", "-1.00"),
            ("2026-01-04", "Thing two", "-2.00"),
            ("2026-01-05", "Thing three", "-3.00"),
            ("2026-01-06", "Thing four", "-4.00"),
        ],
    )
    txs = {
        t["description"]: t["id"]
        for t in client.get(f"{base(alice)}/bank-transactions", headers=alice.headers).json()
    }
    suggester.raw_answers = {
        txs["Thing one"]: ids["5300"],  # valid
        txs["Thing two"]: ids["1000"],  # a bank account: not allowed as a category
        txs["Thing three"]: account_ids(client, bob)["5300"],  # another org's account
        999_999: ids["5300"],  # a transaction id that wasn't asked about
    }

    suggestions = suggest(client, alice)

    assert [(s["bank_transaction_id"], s["account_id"]) for s in suggestions] == [
        (txs["Thing one"], ids["5300"])
    ]


def test_no_llm_answer_means_no_suggestion_not_an_error(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    import_csv(client, alice, [("2026-01-03", "Mystery", "-1.00")])

    assert suggest(client, alice) == []


def test_transactions_with_pending_suggestion_are_not_suggested_again(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    suggester.keyword_to_code = {"zoom": "5500"}
    import_csv(client, alice, [("2026-01-03", "ZOOM.US", "-15.99")])

    first = suggest(client, alice)
    second = suggest(client, alice)

    assert len(first) == 1 and second == []
    assert len(suggester.calls) == 1


# --- Accept / reject / categorize -----------------------------------------------------


def test_accepting_money_out_posts_expense_and_reduces_bank(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    suggester.keyword_to_code = {"github": "5500"}
    import_csv(client, alice, [("2026-01-04", "GITHUB INC", "-21.00")])
    suggestion = suggest(client, alice)[0]

    response = client.post(
        f"{base(alice)}/suggestions/{suggestion['id']}/accept", headers=alice.headers
    )

    assert response.status_code == 200
    tx = response.json()
    assert tx["status"] == "posted" and tx["journal_entry_id"] is not None
    assert balance(client, alice, ids["5500"]) == 2100
    assert balance(client, alice, ids["1000"]) == -2100
    entry = client.get(
        f"{base(alice)}/journal-entries/{tx['journal_entry_id']}", headers=alice.headers
    ).json()
    assert entry["source"] == "bank"
    assert entry["entry_date"] == "2026-01-04"


def test_categorizing_money_in_posts_income_and_increases_bank(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    import_csv(client, alice, [("2026-01-05", "ACME CORP PAYMENT", "1500.00")])
    tx_id = client.get(f"{base(alice)}/bank-transactions", headers=alice.headers).json()[0]["id"]

    response = client.post(
        f"{base(alice)}/bank-transactions/{tx_id}/categorize",
        headers=alice.headers,
        json={"account_id": ids["4000"]},
    )

    assert response.status_code == 200
    assert balance(client, alice, ids["4000"]) == 150000
    assert balance(client, alice, ids["1000"]) == 150000


def test_accept_with_create_rule_makes_next_import_rule_based(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    suggester.keyword_to_code = {"notion": "5500"}
    import_csv(client, alice, [("2026-01-04", "NOTION LABS", "-10.00")])
    suggestion = suggest(client, alice)[0]
    client.post(
        f"{base(alice)}/suggestions/{suggestion['id']}/accept",
        headers=alice.headers,
        json={"create_rule": True},
    )

    rules = client.get(f"{base(alice)}/rules", headers=alice.headers).json()
    import_csv(client, alice, [("2026-02-04", "NOTION LABS", "-10.00")])
    next_suggestions = suggest(client, alice)

    assert [(r["match_field"], r["match_type"], r["pattern"], r["account_id"]) for r in rules] == [
        ("vendor", "equals", "notion labs", ids["5500"])
    ]
    assert [s["source"] for s in next_suggestions] == ["rule"]
    assert len(suggester.calls) == 1  # only the first import needed the LLM


def test_rejecting_keeps_transaction_in_review_and_counts_in_stats(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    suggester.keyword_to_code = {"coffee": "5200", "hotel": "5600"}
    import_csv(
        client,
        alice,
        [
            ("2026-01-03", "COFFEE SHOP", "-4.00"),
            ("2026-01-04", "HOTEL NYC", "-200.00"),
            ("2026-01-05", "COFFEE BAR", "-3.00"),
        ],
    )
    s1, s2, s3 = suggest(client, alice)
    client.post(f"{base(alice)}/suggestions/{s1['id']}/accept", headers=alice.headers)
    client.post(f"{base(alice)}/suggestions/{s2['id']}/accept", headers=alice.headers)
    rejected = client.post(f"{base(alice)}/suggestions/{s3['id']}/reject", headers=alice.headers)

    assert rejected.json()["status"] == "rejected"
    in_review = client.get(
        f"{base(alice)}/bank-transactions", params={"status": "for_review"}, headers=alice.headers
    ).json()
    assert [t["id"] for t in in_review] == [s3["bank_transaction_id"]]
    stats = {
        s["source"]: s
        for s in client.get(f"{base(alice)}/suggestions/stats", headers=alice.headers).json()
    }
    assert (stats["llm"]["accepted"], stats["llm"]["rejected"]) == (2, 1)
    assert stats["llm"]["acceptance_rate"] == pytest.approx(0.6667)
    assert stats["rule"]["acceptance_rate"] is None
    assert balance(client, alice, ids["5200"]) == 400


def test_choosing_a_different_account_rejects_the_suggestion(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    suggester.keyword_to_code = {"amazon": "5300"}
    import_csv(client, alice, [("2026-01-03", "AMAZON MKTP", "-30.00")])
    suggestion = suggest(client, alice)[0]

    client.post(
        f"{base(alice)}/bank-transactions/{suggestion['bank_transaction_id']}/categorize",
        headers=alice.headers,
        json={"account_id": ids["5000"]},
    )

    decided = client.get(
        f"{base(alice)}/suggestions", params={"status": "rejected"}, headers=alice.headers
    ).json()
    assert [d["id"] for d in decided] == [suggestion["id"]]


def test_transaction_can_only_be_posted_once(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    import_csv(client, alice, [("2026-01-03", "RENT", "-1200.00")])
    tx_id = client.get(f"{base(alice)}/bank-transactions", headers=alice.headers).json()[0]["id"]
    url = f"{base(alice)}/bank-transactions/{tx_id}/categorize"

    first = client.post(url, headers=alice.headers, json={"account_id": ids["5400"]})
    second = client.post(url, headers=alice.headers, json={"account_id": ids["5400"]})

    assert first.status_code == 200
    assert second.status_code == 409
    assert balance(client, alice, ids["5400"]) == 120000


def test_cannot_categorize_into_a_bank_account(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    import_csv(client, alice, [("2026-01-03", "TRANSFER", "-100.00")])
    tx_id = client.get(f"{base(alice)}/bank-transactions", headers=alice.headers).json()[0]["id"]

    response = client.post(
        f"{base(alice)}/bank-transactions/{tx_id}/categorize",
        headers=alice.headers,
        json={"account_id": ids["1000"]},
    )

    assert response.status_code == 422


def test_excluded_transaction_stays_out_of_the_ledger(
    client: TestClient, make_user: MakeUser
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    import_csv(client, alice, [("2026-01-03", "TRANSFER TO SAVINGS", "-500.00")])
    tx_id = client.get(f"{base(alice)}/bank-transactions", headers=alice.headers).json()[0]["id"]

    excluded = client.post(
        f"{base(alice)}/bank-transactions/{tx_id}/exclude", headers=alice.headers
    )
    later = client.post(
        f"{base(alice)}/bank-transactions/{tx_id}/categorize",
        headers=alice.headers,
        json={"account_id": ids["5900"]},
    )

    assert excluded.json()["status"] == "excluded"
    assert later.status_code == 409
    assert balance(client, alice, ids["1000"]) == 0


def test_rule_crud(client: TestClient, make_user: MakeUser) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    rule = add_rule(client, alice, pattern="  Google   Workspace ", account_id=ids["5500"])

    updated = client.patch(
        f"{base(alice)}/rules/{rule['id']}", headers=alice.headers, json={"priority": 5}
    ).json()
    deleted = client.delete(f"{base(alice)}/rules/{rule['id']}", headers=alice.headers)

    assert rule["pattern"] == "google workspace"
    assert updated["priority"] == 5
    assert deleted.status_code == 204
    assert client.get(f"{base(alice)}/rules", headers=alice.headers).json() == []


# --- The Anthropic provider, with a fake client (no network) ---------------------------


class _FakeMessages:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result, self.error, self.kwargs = result, error, None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.result


def _provider(messages: _FakeMessages) -> AnthropicSuggester:
    return AnthropicSuggester("claude-opus-5", client=SimpleNamespace(messages=messages))


TXS = [TransactionToCategorize(1, "GITHUB INC", "github inc", "out")]
ACCOUNTS = [AccountOption(7, "5500", "Software", "expense")]


def test_anthropic_provider_maps_parsed_output() -> None:
    parsed = _Choices(
        choices=[
            _Choice(transaction_id=1, account_id=7),
            _Choice(transaction_id=2, account_id=None),
        ]
    )
    messages = _FakeMessages(SimpleNamespace(stop_reason="end_turn", parsed_output=parsed))

    result = _provider(messages).suggest(TXS, ACCOUNTS)

    assert result == {1: 7}
    assert messages.kwargs["model"] == "claude-opus-5"
    assert messages.kwargs["output_format"] is _Choices
    assert "GITHUB INC" in messages.kwargs["messages"][0]["content"]


def test_anthropic_provider_returns_nothing_on_refusal_or_error() -> None:
    refusal = _FakeMessages(SimpleNamespace(stop_reason="refusal", parsed_output=None))
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    down = _FakeMessages(error=anthropic.APIConnectionError(request=request))

    assert _provider(refusal).suggest(TXS, ACCOUNTS) == {}
    assert _provider(down).suggest(TXS, ACCOUNTS) == {}


# --- Accept all ------------------------------------------------------------------------


def _five_coffees(client: TestClient, user: LoggedInUser, suggester: FakeSuggester) -> None:
    suggester.keyword_to_code = {"coffee": "5200"}
    import_csv(client, user, [(f"2026-01-0{i}", f"COFFEE {i}", "-4.00") for i in range(1, 6)])
    assert len(suggest(client, user)) == 5


def test_accept_all_posts_pending_suggestions_up_to_the_cap(
    client: TestClient,
    make_user: MakeUser,
    suggester: FakeSuggester,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    _five_coffees(client, alice, suggester)
    monkeypatch.setattr(categorization_service, "MAX_ACCEPT_ALL", 3)
    url = f"{base(alice)}/suggestions/accept-all"

    first = client.post(url, headers=alice.headers)
    second = client.post(url, headers=alice.headers)

    assert first.json() == {"accepted": 3, "skipped": 0, "remaining": 2}
    assert second.json() == {"accepted": 2, "skipped": 0, "remaining": 0}
    assert balance(client, alice, ids["5200"]) == 2000


def test_accept_all_skips_what_cannot_be_posted_and_carries_on(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    ids = account_ids(client, alice)
    suggester.keyword_to_code = {"coffee": "5200", "train": "5600"}
    import_csv(
        client,
        alice,
        [("2026-01-02", "COFFEE SHOP", "-4.00"), ("2026-01-03", "TRAIN TICKET", "-30.00")],
    )
    suggest(client, alice)
    # Meals gets deactivated after its suggestion was made, so that one can't be posted.
    client.patch(
        f"{base(alice)}/accounts/{ids['5200']}", headers=alice.headers, json={"is_active": False}
    )

    result = client.post(f"{base(alice)}/suggestions/accept-all", headers=alice.headers)

    assert result.json() == {"accepted": 1, "skipped": 1, "remaining": 0}
    assert balance(client, alice, ids["5600"]) == 3000


def test_accept_all_ignores_suggestions_for_transactions_no_longer_in_review(
    client: TestClient, make_user: MakeUser, suggester: FakeSuggester
) -> None:
    alice = make_user("alice@example.com")
    suggester.keyword_to_code = {"coffee": "5200"}
    import_csv(
        client,
        alice,
        [("2026-01-02", "COFFEE ONE", "-4.00"), ("2026-01-03", "COFFEE TWO", "-5.00")],
    )
    first, _ = suggest(client, alice)
    client.post(
        f"{base(alice)}/bank-transactions/{first['bank_transaction_id']}/exclude",
        headers=alice.headers,
    )

    result = client.post(f"{base(alice)}/suggestions/accept-all", headers=alice.headers)

    assert result.json() == {"accepted": 1, "skipped": 0, "remaining": 0}
