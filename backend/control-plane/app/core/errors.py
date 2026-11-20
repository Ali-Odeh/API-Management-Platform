"""Application errors, independent of HTTP.

crud.py raises these. One handler (registered in main.py) turns them into
the plan's error format `{"detail": "..."}` with the right status code.
This keeps business logic free of FastAPI, and every feature answers
errors the same way.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ForbiddenError(AppError):
    """Signed in, but not allowed. The detail is a short code the dashboard
    can act on, e.g. "mfa_required" or "user_disabled"."""

    status_code = status.HTTP_403_FORBIDDEN


class UpstreamError(AppError):
    """A service we depend on (GoTrue) failed, so the change was not made."""

    status_code = status.HTTP_502_BAD_GATEWAY


class NotFoundError(AppError):
    """The record doesn't exist, or belongs to another workspace."""

    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    """The change would break a uniqueness rule (e.g. duplicate name)."""

    status_code = status.HTTP_409_CONFLICT


class UnprocessableError(AppError):
    """The body is well-formed but refers to something unusable,
    e.g. a route pointing to a service that isn't in this workspace."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
