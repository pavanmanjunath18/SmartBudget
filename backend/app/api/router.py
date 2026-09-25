"""Collects every route module under the /api/v1 prefix."""

from fastapi import APIRouter

from app.api import accounts, auth, health, journal, organizations

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(organizations.router)
api_router.include_router(accounts.router)
api_router.include_router(journal.router)
