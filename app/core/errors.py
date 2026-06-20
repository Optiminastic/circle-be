"""Domain error hierarchy + FastAPI exception handlers.

Keeping a small typed error hierarchy lets the API layer translate failures into
consistent, structured HTTP responses without leaking implementation details
(error tolerance + SRP).
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .logging import get_logger

logger = get_logger("curcle.errors")


class AppError(Exception):
    """Base application error."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.__doc__ or "Unexpected error"
        super().__init__(self.detail)


class NotFoundError(AppError):
    """Requested resource was not found."""

    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    """The request payload was invalid."""

    status_code = 422
    code = "validation_error"


class AuthError(AppError):
    """The caller is not authorized to use this endpoint."""

    status_code = 403
    code = "forbidden"


class RateLimitedError(AppError):
    """Too many requests — slow down and retry later."""

    status_code = 429
    code = "rate_limited"


class RepositoryError(AppError):
    """The data store is unavailable or a query failed."""

    status_code = 503
    code = "repository_error"


class StorageError(AppError):
    """The object storage backend is unavailable or rejected the operation."""

    status_code = 502
    code = "storage_error"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.exception("AppError: %s", exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "detail": exc.detail})

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(status_code=500, content={"error": "internal_error", "detail": "Internal server error"})
