"""Request/response bodies for organizations."""

from datetime import datetime

from pydantic import BaseModel, Field


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OrganizationRead(BaseModel):
    id: int
    name: str
    role: str  # the caller's role in this organization
    created_at: datetime
