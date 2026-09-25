"""Errors raised by the services layer.

Services know nothing about HTTP. The API layer maps these to status codes in one
place (see app.main), so every route reports the same error the same way.
"""


class ServiceError(Exception):
    """Base class for expected, user-facing errors."""


class NotFoundError(ServiceError):
    """The resource does not exist, or belongs to an organization the caller can't see."""


class ConflictError(ServiceError):
    """The request clashes with existing data, e.g. an email that is already registered."""


class AuthenticationError(ServiceError):
    """Credentials or token are missing or wrong."""


class BusinessRuleError(ServiceError):
    """The request is well-formed but breaks a bookkeeping rule, e.g. an unbalanced entry."""
