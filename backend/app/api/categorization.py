"""Categorization rules, suggestions (rule / cache / LLM) and posting bank transactions."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_org_context
from app.db.session import get_db
from app.models import BankTransaction, CategoryRule, CategorySuggestion, SuggestionStatus
from app.schemas.bank import BankTransactionRead
from app.schemas.categorization import (
    AcceptAllResult,
    CategorizeRequest,
    DecisionRequest,
    RuleCreate,
    RuleRead,
    RuleUpdate,
    SourceStatsRead,
    SuggestionRead,
    SuggestRequest,
)
from app.services import categorization_service
from app.services.ai_service import CategorySuggester, get_suggester

router = APIRouter(prefix="/orgs/{org_id}", tags=["categorization"])


def to_suggestion_read(suggestion: CategorySuggestion, tx: BankTransaction) -> SuggestionRead:
    return SuggestionRead(
        id=suggestion.id,
        bank_transaction_id=tx.id,
        account_id=suggestion.account_id,
        source=suggestion.source,
        rule_id=suggestion.rule_id,
        status=suggestion.status,
        posted_date=tx.posted_date,
        amount_cents=tx.amount_cents,
        description=tx.description,
        normalized_vendor=tx.normalized_vendor,
    )


# --- Rules ---


@router.get("/rules", response_model=list[RuleRead])
def list_rules(
    ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> list[CategoryRule]:
    return categorization_service.list_rules(db, ctx.org_id)


@router.post("/rules", response_model=RuleRead, status_code=status.HTTP_201_CREATED)
def create_rule(
    body: RuleCreate, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> CategoryRule:
    return categorization_service.create_rule(db, ctx.org_id, **body.model_dump())


@router.patch("/rules/{rule_id}", response_model=RuleRead)
def update_rule(
    rule_id: int,
    body: RuleUpdate,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> CategoryRule:
    return categorization_service.update_rule(
        db, ctx.org_id, rule_id, body.account_id, body.priority, body.is_active
    )


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(
    rule_id: int, ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> Response:
    categorization_service.delete_rule(db, ctx.org_id, rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Suggestions ---


@router.post("/bank-transactions/suggest", response_model=list[SuggestionRead])
def suggest(
    body: SuggestRequest | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
    suggester: CategorySuggester = Depends(get_suggester),
) -> list[SuggestionRead]:
    """Suggest accounts: rules first, then the vendor cache, then the LLM for the rest."""
    ids = body.transaction_ids if body else None
    created = categorization_service.suggest(db, ctx.org_id, suggester, ids)
    # The transactions are already in the session, so db.get doesn't query again.
    return [to_suggestion_read(s, db.get(BankTransaction, s.bank_transaction_id)) for s in created]


@router.get("/suggestions", response_model=list[SuggestionRead])
def list_suggestions(
    status: SuggestionStatus | None = SuggestionStatus.PENDING,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> list[SuggestionRead]:
    rows = categorization_service.list_suggestions(db, ctx.org_id, status)
    return [to_suggestion_read(s, tx) for s, tx in rows]


@router.get("/suggestions/stats", response_model=list[SourceStatsRead])
def suggestion_stats(
    ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> list[SourceStatsRead]:
    return [
        SourceStatsRead(
            source=s.source,
            accepted=s.accepted,
            rejected=s.rejected,
            pending=s.pending,
            acceptance_rate=s.acceptance_rate,
        )
        for s in categorization_service.suggestion_stats(db, ctx.org_id)
    ]


@router.post("/suggestions/accept-all", response_model=AcceptAllResult)
def accept_all_suggestions(
    ctx: OrgContext = Depends(get_org_context), db: Session = Depends(get_db)
) -> AcceptAllResult:
    """Post every pending suggestion for transactions still in review."""
    accepted, skipped, remaining = categorization_service.accept_all(db, ctx.org_id, ctx.user.id)
    return AcceptAllResult(accepted=accepted, skipped=skipped, remaining=remaining)


@router.post("/suggestions/{suggestion_id}/accept", response_model=BankTransactionRead)
def accept_suggestion(
    suggestion_id: int,
    body: DecisionRequest | None = None,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> BankTransaction:
    """Post the transaction with the suggested account; optionally create a rule."""
    create_rule = body.create_rule if body else False
    return categorization_service.accept_suggestion(
        db, ctx.org_id, ctx.user.id, suggestion_id, create_rule
    )


@router.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionRead)
def reject_suggestion(
    suggestion_id: int,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> SuggestionRead:
    suggestion = categorization_service.reject_suggestion(
        db, ctx.org_id, ctx.user.id, suggestion_id
    )
    tx = db.get(BankTransaction, suggestion.bank_transaction_id)
    return to_suggestion_read(suggestion, tx)


# --- Posting ---


@router.post("/bank-transactions/{transaction_id}/categorize", response_model=BankTransactionRead)
def categorize(
    transaction_id: int,
    body: CategorizeRequest,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> BankTransaction:
    """Pick an account yourself and post the transaction to the ledger."""
    return categorization_service.categorize(
        db, ctx.org_id, ctx.user.id, transaction_id, body.account_id, body.create_rule
    )


@router.post("/bank-transactions/{transaction_id}/exclude", response_model=BankTransactionRead)
def exclude(
    transaction_id: int,
    ctx: OrgContext = Depends(get_org_context),
    db: Session = Depends(get_db),
) -> BankTransaction:
    """Leave the transaction out of the books (e.g. a transfer that's already recorded)."""
    return categorization_service.exclude(db, ctx.org_id, transaction_id)
