"""Request/response bodies for rules, suggestions and categorizing transactions."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import MatchField, MatchType


class RuleCreate(BaseModel):
    pattern: str = Field(min_length=1, max_length=200)
    account_id: int
    match_field: MatchField = MatchField.DESCRIPTION
    match_type: MatchType = MatchType.CONTAINS
    priority: int = Field(default=100, ge=0, le=10_000)


class RuleUpdate(BaseModel):
    account_id: int | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None


class RuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    pattern: str
    account_id: int
    match_field: MatchField
    match_type: MatchType
    priority: int
    is_active: bool
    created_at: datetime


class SuggestRequest(BaseModel):
    # Omit to process every transaction in review that has no pending suggestion.
    transaction_ids: list[int] | None = None


class SuggestionRead(BaseModel):
    id: int
    bank_transaction_id: int
    account_id: int
    source: str
    rule_id: int | None
    status: str
    posted_date: date
    amount_cents: int
    description: str
    normalized_vendor: str


class DecisionRequest(BaseModel):
    create_rule: bool = False  # also add a "vendor equals X" rule for next time


class CategorizeRequest(BaseModel):
    account_id: int
    create_rule: bool = False


class SourceStatsRead(BaseModel):
    source: str
    accepted: int
    rejected: int
    pending: int
    acceptance_rate: float | None  # accepted / (accepted + rejected); None if no decisions
