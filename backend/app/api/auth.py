"""Signup, login and the current user."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.organizations import to_org_read
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import User
from app.schemas.auth import LoginRequest, MeResponse, SignupRequest, TokenResponse, UserRead
from app.services import auth_service, demo_service, org_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> User:
    """Register a user together with their first organization."""
    return auth_service.signup(db, body.email, body.password, body.organization_name)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Exchange email and password for a bearer token."""
    user = auth_service.authenticate(db, body.email, body.password)
    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeResponse)
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> MeResponse:
    """The logged-in user and the organizations they belong to."""
    memberships = org_service.list_memberships(db, user.id)
    return MeResponse(
        user=UserRead.model_validate(user),
        organizations=[to_org_read(m) for m in memberships],
        demo=demo_service.is_demo_user(settings, user),
    )
