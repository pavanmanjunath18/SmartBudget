"""Shared route dependencies: the logged-in user and the organization being accessed.

Every org-scoped route takes `ctx: OrgContext = Depends(get_org_context)`. That one
dependency is the tenancy gate: it proves the caller belongs to the org in the URL,
and services then filter every query by `ctx.org_id`.
"""

from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Membership, User
from app.services import org_service
from app.services.errors import AuthenticationError, NotFoundError

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the user from the `Authorization: Bearer <token>` header, or 401."""
    if credentials is None:
        raise AuthenticationError("Not authenticated.")
    user_id = decode_access_token(credentials.credentials)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None:
        raise AuthenticationError("Invalid or expired token.")
    return user


@dataclass(frozen=True)
class OrgContext:
    """Who is calling, and which organization they are acting in."""

    user: User
    membership: Membership

    @property
    def org_id(self) -> int:
        return self.membership.org_id

    @property
    def role(self) -> str:
        return self.membership.role


def get_org_context(
    org_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrgContext:
    """Allow the request only if the user is a member of `org_id`.

    Non-members get 404 (not 403), so nobody can probe which org ids exist.
    """
    membership = org_service.get_membership(db, user.id, org_id)
    if membership is None:
        raise NotFoundError("Organization not found.")
    return OrgContext(user=user, membership=membership)
