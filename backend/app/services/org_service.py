"""Organizations and memberships."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Membership, Organization, Role, User
from app.services import account_service

logger = logging.getLogger(__name__)


def add_organization(db: Session, user: User, name: str) -> Membership:
    """Create an organization with `user` as its owner and a default chart of accounts.

    Seeding is an explicit call here rather than a database signal/hook, so it is
    obvious when it happens. Does not commit.
    """
    org = Organization(name=name.strip())
    membership = Membership(user=user, organization=org, role=Role.OWNER)
    db.add_all([org, membership])
    db.flush()
    account_service.seed_default_accounts(db, org.id)
    logger.info("org_created org_id=%s owner_user_id=%s", org.id, user.id)
    return membership


def create_organization(db: Session, user: User, name: str) -> Membership:
    """Create an organization owned by `user` and commit."""
    membership = add_organization(db, user, name)
    db.commit()
    return membership


def list_memberships(db: Session, user_id: int) -> list[Membership]:
    """All organizations the user belongs to, oldest first."""
    stmt = (
        select(Membership)
        .join(Membership.organization)
        .where(Membership.user_id == user_id)
        .order_by(Organization.id)
    )
    return list(db.scalars(stmt))


def get_membership(db: Session, user_id: int, org_id: int) -> Membership | None:
    """The user's membership in one organization, or None if they are not a member."""
    stmt = select(Membership).where(Membership.user_id == user_id, Membership.org_id == org_id)
    return db.scalars(stmt).first()
