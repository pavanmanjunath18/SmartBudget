"""Categorize imported bank transactions and post them to the ledger.

Order of suggestion sources, cheapest and most trusted first:
1. the organization's own rules,
2. the vendor cache (what the user confirmed for this vendor before),
3. the LLM, only for whatever is still unmatched.
Nothing reaches the ledger until a person accepts a suggestion or picks an account.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountSubtype,
    BankTransaction,
    BankTransactionStatus,
    CategoryRule,
    CategorySuggestion,
    EntrySource,
    MatchField,
    MatchType,
    SuggestionSource,
    SuggestionStatus,
    VendorCategoryCache,
)
from app.services import ledger_service
from app.services.ai_service import AccountOption, CategorySuggester, TransactionToCategorize
from app.services.errors import BusinessRuleError, ConflictError, NotFoundError
from app.services.ledger_service import LineInput

logger = logging.getLogger(__name__)

MAX_SUGGEST_BATCH = 200


# --- Rules ---------------------------------------------------------------------------


def rule_matches(rule: CategoryRule, tx: BankTransaction) -> bool:
    """Case-insensitive match of one rule against one transaction."""
    text = tx.normalized_vendor if rule.match_field == MatchField.VENDOR else tx.description
    text = text.lower()
    if rule.match_type == MatchType.EQUALS:
        return text == rule.pattern
    if rule.match_type == MatchType.STARTS_WITH:
        return text.startswith(rule.pattern)
    return rule.pattern in text


def first_matching_rule(rules: list[CategoryRule], tx: BankTransaction) -> CategoryRule | None:
    """Rules are checked by priority (then age); the first match wins."""
    for rule in sorted(rules, key=lambda r: (r.priority, r.id)):
        if rule.is_active and rule_matches(rule, tx):
            return rule
    return None


def _category_account(db: Session, org_id: int, account_id: int) -> Account:
    """An account transactions may be categorized into: this org's, active, not a bank."""
    account = db.scalars(
        select(Account).where(Account.org_id == org_id, Account.id == account_id)
    ).first()
    if account is None:
        raise BusinessRuleError("Account not found.")
    if not account.is_active:
        raise BusinessRuleError("Account is inactive.")
    if account.subtype == AccountSubtype.BANK:
        raise BusinessRuleError("Pick a category account, not a bank account.")
    return account


def list_rules(db: Session, org_id: int) -> list[CategoryRule]:
    stmt = (
        select(CategoryRule)
        .where(CategoryRule.org_id == org_id)
        .order_by(CategoryRule.priority, CategoryRule.id)
    )
    return list(db.scalars(stmt))


def get_rule(db: Session, org_id: int, rule_id: int) -> CategoryRule:
    rule = db.scalars(
        select(CategoryRule).where(CategoryRule.org_id == org_id, CategoryRule.id == rule_id)
    ).first()
    if rule is None:
        raise NotFoundError("Rule not found.")
    return rule


def add_rule(
    db: Session,
    org_id: int,
    pattern: str,
    account_id: int,
    match_field: MatchField = MatchField.DESCRIPTION,
    match_type: MatchType = MatchType.CONTAINS,
    priority: int = 100,
) -> CategoryRule:
    """Add a rule to the session. Does not commit."""
    _category_account(db, org_id, account_id)
    pattern = " ".join(pattern.lower().split())
    if not pattern:
        raise BusinessRuleError("Rule pattern can't be empty.")
    rule = CategoryRule(
        org_id=org_id,
        pattern=pattern,
        account_id=account_id,
        match_field=match_field,
        match_type=match_type,
        priority=priority,
    )
    db.add(rule)
    db.flush()
    return rule


def create_rule(db: Session, org_id: int, **fields: object) -> CategoryRule:
    rule = add_rule(db, org_id, **fields)
    db.commit()
    return rule


def update_rule(
    db: Session,
    org_id: int,
    rule_id: int,
    account_id: int | None,
    priority: int | None,
    is_active: bool | None,
) -> CategoryRule:
    rule = get_rule(db, org_id, rule_id)
    if account_id is not None:
        _category_account(db, org_id, account_id)
        rule.account_id = account_id
    if priority is not None:
        rule.priority = priority
    if is_active is not None:
        rule.is_active = is_active
    db.commit()
    return rule


def delete_rule(db: Session, org_id: int, rule_id: int) -> None:
    """Rules are settings, not financial records, so they can be deleted."""
    db.delete(get_rule(db, org_id, rule_id))
    db.commit()


# --- Suggestions ---------------------------------------------------------------------


def suggest(
    db: Session,
    org_id: int,
    suggester: CategorySuggester,
    transaction_ids: list[int] | None = None,
) -> list[CategorySuggestion]:
    """Create pending suggestions for transactions in review that don't have one yet."""
    has_pending = (
        select(CategorySuggestion.id)
        .where(
            CategorySuggestion.bank_transaction_id == BankTransaction.id,
            CategorySuggestion.status == SuggestionStatus.PENDING,
        )
        .exists()
    )
    stmt = (
        select(BankTransaction)
        .where(
            BankTransaction.org_id == org_id,
            BankTransaction.status == BankTransactionStatus.FOR_REVIEW,
            ~has_pending,
        )
        .order_by(BankTransaction.posted_date, BankTransaction.id)
        .limit(MAX_SUGGEST_BATCH)
    )
    if transaction_ids is not None:
        stmt = stmt.where(BankTransaction.id.in_(transaction_ids))
    transactions = list(db.scalars(stmt))
    if not transactions:
        return []

    rules = list_rules(db, org_id)
    vendors = {tx.normalized_vendor for tx in transactions}
    cache = {
        row.normalized_vendor: row.account_id
        for row in db.scalars(
            select(VendorCategoryCache).where(
                VendorCategoryCache.org_id == org_id,
                VendorCategoryCache.normalized_vendor.in_(vendors),
            )
        )
    }

    created: list[CategorySuggestion] = []
    unmatched: list[BankTransaction] = []
    for tx in transactions:
        rule = first_matching_rule(rules, tx)
        if rule is not None:
            created.append(_suggestion(org_id, tx, rule.account_id, SuggestionSource.RULE, rule.id))
        elif tx.normalized_vendor in cache:
            created.append(
                _suggestion(org_id, tx, cache[tx.normalized_vendor], SuggestionSource.CACHE)
            )
        else:
            unmatched.append(tx)

    if unmatched:
        created.extend(_llm_suggestions(db, org_id, suggester, unmatched))

    db.add_all(created)
    db.commit()
    logger.info(
        "suggestions_created org_id=%s total=%s rule=%s cache=%s llm=%s llm_requested=%s",
        org_id,
        len(created),
        sum(s.source == SuggestionSource.RULE for s in created),
        sum(s.source == SuggestionSource.CACHE for s in created),
        sum(s.source == SuggestionSource.LLM for s in created),
        len(unmatched),
    )
    return created


def _suggestion(
    org_id: int,
    tx: BankTransaction,
    account_id: int,
    source: SuggestionSource,
    rule_id: int | None = None,
) -> CategorySuggestion:
    return CategorySuggestion(
        org_id=org_id,
        bank_transaction_id=tx.id,
        account_id=account_id,
        source=source,
        rule_id=rule_id,
    )


def _llm_suggestions(
    db: Session, org_id: int, suggester: CategorySuggester, transactions: list[BankTransaction]
) -> list[CategorySuggestion]:
    accounts = [
        a
        for a in db.scalars(
            select(Account).where(Account.org_id == org_id, Account.is_active.is_(True))
        )
        if a.subtype != AccountSubtype.BANK
    ]
    options = [AccountOption(a.id, a.code, a.name, a.type) for a in accounts]
    items = [
        TransactionToCategorize(
            tx.id, tx.description, tx.normalized_vendor, "in" if tx.amount_cents > 0 else "out"
        )
        for tx in transactions
    ]
    answers = suggester.suggest(items, options)
    # Never trust the model's ids: keep only real transactions and allowed accounts.
    allowed = {a.id for a in accounts}
    by_id = {tx.id: tx for tx in transactions}
    return [
        _suggestion(org_id, by_id[tx_id], account_id, SuggestionSource.LLM)
        for tx_id, account_id in answers.items()
        if tx_id in by_id and account_id in allowed
    ]


def list_suggestions(
    db: Session, org_id: int, status: SuggestionStatus | None = None
) -> list[tuple[CategorySuggestion, BankTransaction]]:
    stmt = (
        select(CategorySuggestion, BankTransaction)
        .join(BankTransaction, CategorySuggestion.bank_transaction_id == BankTransaction.id)
        .where(CategorySuggestion.org_id == org_id)
        .order_by(BankTransaction.posted_date.desc(), CategorySuggestion.id.desc())
    )
    if status is not None:
        stmt = stmt.where(CategorySuggestion.status == status)
    return [(s, tx) for s, tx in db.execute(stmt).all()]


def _get_suggestion(db: Session, org_id: int, suggestion_id: int) -> CategorySuggestion:
    suggestion = db.scalars(
        select(CategorySuggestion).where(
            CategorySuggestion.org_id == org_id, CategorySuggestion.id == suggestion_id
        )
    ).first()
    if suggestion is None:
        raise NotFoundError("Suggestion not found.")
    if suggestion.status != SuggestionStatus.PENDING:
        raise ConflictError(f"Suggestion is already {suggestion.status}.")
    return suggestion


def accept_suggestion(
    db: Session, org_id: int, user_id: int, suggestion_id: int, create_rule: bool
) -> BankTransaction:
    suggestion = _get_suggestion(db, org_id, suggestion_id)
    return categorize(
        db, org_id, user_id, suggestion.bank_transaction_id, suggestion.account_id, create_rule
    )


def reject_suggestion(
    db: Session, org_id: int, user_id: int, suggestion_id: int
) -> CategorySuggestion:
    suggestion = _get_suggestion(db, org_id, suggestion_id)
    _decide(suggestion, SuggestionStatus.REJECTED, user_id)
    db.commit()
    return suggestion


def _decide(suggestion: CategorySuggestion, status: SuggestionStatus, user_id: int) -> None:
    suggestion.status = status
    suggestion.decided_by_user_id = user_id
    suggestion.decided_at = datetime.now(UTC)


@dataclass(frozen=True)
class SourceStats:
    source: str
    accepted: int
    rejected: int
    pending: int

    @property
    def acceptance_rate(self) -> float | None:
        decided = self.accepted + self.rejected
        return round(self.accepted / decided, 4) if decided else None


def suggestion_stats(db: Session, org_id: int) -> list[SourceStats]:
    """Accepted / rejected / pending counts per source, from real decisions only."""
    rows = db.execute(
        select(CategorySuggestion.source, CategorySuggestion.status, func.count())
        .where(CategorySuggestion.org_id == org_id)
        .group_by(CategorySuggestion.source, CategorySuggestion.status)
    ).all()
    counts: dict[tuple[str, str], int] = {(src, st): n for src, st, n in rows}
    return [
        SourceStats(
            source=source,
            accepted=counts.get((source, SuggestionStatus.ACCEPTED), 0),
            rejected=counts.get((source, SuggestionStatus.REJECTED), 0),
            pending=counts.get((source, SuggestionStatus.PENDING), 0),
        )
        for source in SuggestionSource
    ]


# --- Posting -------------------------------------------------------------------------


def _lock_for_review(db: Session, org_id: int, transaction_id: int) -> BankTransaction:
    """Load the transaction with a row lock so two requests can't both post it."""
    tx = db.scalars(
        select(BankTransaction)
        .where(BankTransaction.org_id == org_id, BankTransaction.id == transaction_id)
        .with_for_update()
    ).first()
    if tx is None:
        raise NotFoundError("Bank transaction not found.")
    if tx.status != BankTransactionStatus.FOR_REVIEW:
        raise ConflictError(f"Transaction is already {tx.status}.")
    return tx


def categorize(
    db: Session,
    org_id: int,
    user_id: int,
    transaction_id: int,
    account_id: int,
    create_rule: bool = False,
) -> BankTransaction:
    """Post a bank transaction to the ledger under `account_id`.

    Money out:  debit the category (e.g. Software), credit the bank.
    Money in:   debit the bank, credit the category (e.g. Sales).
    With the signed convention both cases are the same two lines: the bank line gets
    the transaction amount and the category line gets its negative.

    Also records the decision on any pending suggestion, remembers the vendor in the
    cache, and optionally creates a "vendor equals X" rule. One commit for all of it.
    """
    tx = _lock_for_review(db, org_id, transaction_id)
    _category_account(db, org_id, account_id)

    entry = ledger_service.record_entry(
        db,
        org_id,
        user_id,
        tx.posted_date,
        memo=tx.description,
        lines=[
            LineInput(tx.account_id, tx.amount_cents, tx.description),
            LineInput(account_id, -tx.amount_cents, tx.description),
        ],
        source=EntrySource.BANK,
    )
    tx.status = BankTransactionStatus.POSTED
    tx.journal_entry_id = entry.id

    pending = db.scalars(
        select(CategorySuggestion).where(
            CategorySuggestion.bank_transaction_id == tx.id,
            CategorySuggestion.status == SuggestionStatus.PENDING,
        )
    ).all()
    for suggestion in pending:
        status = (
            SuggestionStatus.ACCEPTED
            if suggestion.account_id == account_id
            else SuggestionStatus.REJECTED
        )
        _decide(suggestion, status, user_id)

    _remember_vendor(db, org_id, tx.normalized_vendor, account_id)
    if create_rule:
        _ensure_vendor_rule(db, org_id, tx.normalized_vendor, account_id)
    db.commit()
    logger.info("bank_tx_categorized org_id=%s tx_id=%s entry_id=%s", org_id, tx.id, entry.id)
    return tx


def exclude(db: Session, org_id: int, transaction_id: int) -> BankTransaction:
    """Mark a transaction as not for the books (e.g. a transfer already recorded)."""
    tx = _lock_for_review(db, org_id, transaction_id)
    tx.status = BankTransactionStatus.EXCLUDED
    db.commit()
    return tx


def _remember_vendor(db: Session, org_id: int, vendor: str, account_id: int) -> None:
    """Upsert vendor -> account. Only user-confirmed choices go in the cache, so a wrong
    LLM guess never spreads. A single INSERT ... ON CONFLICT is safe under concurrency."""
    stmt = insert(VendorCategoryCache).values(
        org_id=org_id, normalized_vendor=vendor, account_id=account_id, times_confirmed=1
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_vendor_cache_org_vendor",
        set_={
            "account_id": stmt.excluded.account_id,
            # Same account again: count it. A different account: start over at 1.
            "times_confirmed": case(
                (
                    VendorCategoryCache.account_id == stmt.excluded.account_id,
                    VendorCategoryCache.times_confirmed + 1,
                ),
                else_=1,
            ),
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)


def _ensure_vendor_rule(db: Session, org_id: int, vendor: str, account_id: int) -> None:
    exists = db.scalars(
        select(CategoryRule).where(
            CategoryRule.org_id == org_id,
            CategoryRule.match_field == MatchField.VENDOR,
            CategoryRule.match_type == MatchType.EQUALS,
            CategoryRule.pattern == vendor,
        )
    ).first()
    if exists is None:
        add_rule(db, org_id, vendor, account_id, MatchField.VENDOR, MatchType.EQUALS)
