"""Request and response shapes for /api/v1/routes."""

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.core.schemas import PartialUpdate
from app.routes.models import HTTP_METHODS

_PATH_CHARS = re.compile(r"[A-Za-z0-9._~/-]*")  # URL-safe characters only

# Paths the gateway answers itself (backend/gateway/internal/server). A route
# under one of them would be created but never receive a request. v2 adds
# "/metrics".
RESERVED_PATH_PREFIXES = ("/health",)


def _is_reserved(path: str) -> bool:
    """True for '/health' and '/health/db', not for '/healthy' (segment boundary)."""
    return any(path == r or path.startswith(r + "/") for r in RESERVED_PATH_PREFIXES)


def _normalize_path_prefix(value: str) -> str:
    """'/v1/orders/' -> '/v1/orders'. Rejects anything the gateway can't match safely.

    Case is kept on purpose: in HTTP, /V1 and /v1 are different paths.
    """
    if not value.startswith("/"):
        raise ValueError("path_prefix must start with '/'")
    if not _PATH_CHARS.fullmatch(value):
        raise ValueError(
            "path_prefix may only contain letters, digits and '/', '-', '_', '.', '~' "
            "(no query string, fragment or spaces)"
        )
    if "//" in value:
        raise ValueError("path_prefix must not contain '//'")
    if any(segment in {".", ".."} for segment in value.split("/")):
        raise ValueError("path_prefix must not contain '.' or '..' segments")
    normalized = value.rstrip("/") or "/"
    if _is_reserved(normalized):
        raise ValueError(
            f"path_prefix '{normalized}' is reserved by the gateway "
            f"(reserved: {', '.join(RESERVED_PATH_PREFIXES)})"
        )
    return normalized


def _normalize_methods(methods: list[str]) -> list[str]:
    """['get', 'Post', 'GET'] -> ['GET', 'POST']: uppercase, no duplicates, order kept."""
    normalized = list(dict.fromkeys(m.strip().upper() for m in methods))
    unknown = [m for m in normalized if m not in HTTP_METHODS]
    if unknown:
        raise ValueError(f"unknown HTTP methods {unknown}; allowed: {', '.join(HTTP_METHODS)}")
    return normalized


PathPrefix = Annotated[
    str,
    StringConstraints(min_length=1, max_length=255),
    AfterValidator(_normalize_path_prefix),
    Field(
        description="Requests whose path starts with this go to the service",
        examples=["/v1/orders"],
    ),
]
Methods = Annotated[
    list[str],
    Field(min_length=1, max_length=len(HTTP_METHODS), examples=[["GET", "POST"]]),
    AfterValidator(_normalize_methods),
]


class RouteCreate(BaseModel):
    service_id: uuid.UUID
    path_prefix: PathPrefix
    methods: Methods
    strip_prefix: bool = Field(
        default=False, description="Remove path_prefix before forwarding to the service"
    )
    auth_required: bool = Field(default=True, description="Require a valid X-API-Key")
    is_active: bool = True


class RouteUpdate(PartialUpdate):
    service_id: uuid.UUID | None = None
    path_prefix: PathPrefix | None = None
    methods: Methods | None = None
    strip_prefix: bool | None = None
    auth_required: bool | None = None
    is_active: bool | None = None


class RouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    path_prefix: str
    methods: list[str]
    strip_prefix: bool
    auth_required: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
