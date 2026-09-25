"""Organizations the caller belongs to."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import OrgContext, get_current_user, get_org_context
from app.db.session import get_db
from app.models import Membership, User
from app.schemas.organization import OrganizationCreate, OrganizationRead
from app.services import org_service

router = APIRouter(prefix="/orgs", tags=["organizations"])


def to_org_read(membership: Membership) -> OrganizationRead:
    """Combine an organization with the caller's role in it."""
    org = membership.organization
    return OrganizationRead(
        id=org.id, name=org.name, role=membership.role, created_at=org.created_at
    )


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
def create_org(
    body: OrganizationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrganizationRead:
    """Create another organization; the caller becomes its owner."""
    return to_org_read(org_service.create_organization(db, user, body.name))


@router.get("", response_model=list[OrganizationRead])
def list_orgs(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[OrganizationRead]:
    """Organizations the caller is a member of."""
    return [to_org_read(m) for m in org_service.list_memberships(db, user.id)]


@router.get("/{org_id}", response_model=OrganizationRead)
def get_org(ctx: OrgContext = Depends(get_org_context)) -> OrganizationRead:
    """One organization. 404 unless the caller is a member."""
    return to_org_read(ctx.membership)
