"""FastAPI application entry point: `uvicorn app.main:app --reload`."""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.errors import (
    AuthenticationError,
    BusinessRuleError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceError,
)

# One place that turns service errors into HTTP responses.
_STATUS_BY_ERROR: dict[type[ServiceError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    ForbiddenError: status.HTTP_403_FORBIDDEN,
    BusinessRuleError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


async def _service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    status_code = _STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
    headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
    return JSONResponse({"detail": str(exc)}, status_code=status_code, headers=headers)


def create_app() -> FastAPI:
    """Build the FastAPI app. A factory keeps tests free to create fresh instances."""
    settings = get_settings()
    configure_logging(settings.log_level)
    # Docs live under /api so they work behind the same nginx proxy as the API.
    app = FastAPI(title=settings.app_name, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_exception_handler(ServiceError, _service_error_handler)
    app.include_router(api_router)
    return app


app = create_app()
