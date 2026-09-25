"""Categorization: user rules, a vendor -> account cache, and suggestions awaiting review."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MatchField(StrEnum):
    DESCRIPTION = "description"  # the raw bank text, lower-cased
    VENDOR = "vendor"  # the cleaned-up vendor name


class MatchType(StrEnum):
    CONTAINS = "contains"
    EQUALS = "equals"
    STARTS_WITH = "starts_with"


class SuggestionSource(StrEnum):
    RULE = "rule"
    CACHE = "cache"
    LLM = "llm"


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class CategoryRule(Base):
    """A user rule, e.g. description contains "aws" -> Software. Lower priority runs first."""

    __tablename__ = "category_rules"
    __table_args__ = (Index("ix_category_rules_org_priority", "org_id", "priority"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    match_field: Mapped[str] = mapped_column(String(20), default=MatchField.DESCRIPTION)
    match_type: Mapped[str] = mapped_column(String(20), default=MatchType.CONTAINS)
    pattern: Mapped[str] = mapped_column(String(200))  # stored lower-cased
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VendorCategoryCache(Base):
    """The account a user last confirmed for a vendor. Checked before asking the LLM."""

    __tablename__ = "vendor_category_cache"
    __table_args__ = (
        UniqueConstraint("org_id", "normalized_vendor", name="uq_vendor_cache_org_vendor"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    normalized_vendor: Mapped[str] = mapped_column(String(200))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    times_confirmed: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CategorySuggestion(Base):
    """A proposed account for a bank transaction. The user accepts or rejects it."""

    __tablename__ = "category_suggestions"
    __table_args__ = (Index("ix_suggestions_org_status", "org_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"))
    bank_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("bank_transactions.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    source: Mapped[str] = mapped_column(String(10))
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("category_rules.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(10), default=SuggestionStatus.PENDING)
    decided_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
