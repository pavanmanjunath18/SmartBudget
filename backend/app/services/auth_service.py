"""Signup and login."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models import User
from app.services import org_service
from app.services.errors import AuthenticationError, ConflictError

logger = logging.getLogger(__name__)


def normalize_email(email: str) -> str:
    """Emails are compared case-insensitively, so store them lower-cased."""
    return email.strip().lower()


def signup(db: Session, email: str, password: str, organization_name: str) -> User:
    """Create a user and their first organization in one transaction.

    Relies on the unique constraint on users.email instead of a "check then insert",
    so two simultaneous signups with the same email can't both succeed.
    """
    user = User(email=normalize_email(email), password_hash=hash_password(password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("An account with this email already exists.") from exc
    org_service.add_organization(db, user, organization_name)
    db.commit()
    logger.info("user_signed_up user_id=%s", user.id)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    """Return the user if the credentials are right.

    Wrong email and wrong password give the same error, so the response doesn't
    reveal which emails are registered.
    """
    user = db.scalars(select(User).where(User.email == normalize_email(email))).first()
    if not verify_password(password, user.password_hash if user else None) or user is None:
        logger.info("login_failed")
        raise AuthenticationError("Incorrect email or password.")
    logger.info("login_succeeded user_id=%s", user.id)
    return user
