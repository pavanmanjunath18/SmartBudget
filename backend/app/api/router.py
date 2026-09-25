"""Collects every route module under the /api/v1 prefix."""

from fastapi import APIRouter

from app.api import (
    accounts,
    auth,
    categorization,
    health,
    imports,
    invoicing,
    journal,
    organizations,
    reconciliation,
    reports,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(accounts.router)
api_router.include_router(journal.router)
api_router.include_router(imports.router)
api_router.include_router(categorization.router)
api_router.include_router(invoicing.router)
api_router.include_router(reports.router)
api_router.include_router(reconciliation.router)
