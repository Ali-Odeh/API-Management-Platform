"""Request and response shapes for /api/v1/workspaces/{slug}/routes."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.core.config import get_settings
from app.core.schemas import PartialUpdate
from app.routes.models import DEFAULT_CORS_MAX_AGE, HTTP_METHODS, MAX_CORS_MAX_AGE

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


# ---- Body size limit (decision 16)


def _within_ceiling(value: int) -> int:
    ceiling = get_settings().max_request_body_ceiling
    if value > ceiling:
        raise ValueError(f"max_body_bytes may be at most {ceiling} (MAX_REQUEST_BODY_CEILING)")
    return value


MaxBodyBytes = Annotated[
    int,
    Field(
        ge=1,
        description="Largest request body the gateway lets through, in bytes "
        "(null: the platform default)",
        examples=[1048576],
    ),
    AfterValidator(_within_ceiling),
]


# ---- CORS (decision 15)

_DEFAULT_PORTS = {"http": 80, "https": 443}
# An HTTP header name (RFC 9110 token).
_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
MAX_CORS_ENTRIES = 50


def _normalize_origin(value: str) -> str:
    """'HTTPS://App.Example.com:443/' -> 'https://app.example.com', as browsers send it.

    '*' (any origin) stays as is.
    """
    if value == "*":
        return value
    parts = urlsplit(value.strip())
    try:
        port = parts.port
    except ValueError:
        raise ValueError(f"origin {value!r} has an invalid port") from None
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise ValueError(f"origin {value!r} must be '*' or http(s)://host[:port]")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        raise ValueError(f"origin {value!r} must be only scheme, host and port")
    origin = f"{parts.scheme}://{parts.hostname}"
    if port is not None and port != _DEFAULT_PORTS[parts.scheme]:
        origin += f":{port}"
    return origin


def _normalize_origins(origins: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(_normalize_origin(o) for o in origins))
    if "*" in normalized and len(normalized) > 1:
        raise ValueError("'*' (any origin) must be the only origin")
    return normalized


def _normalize_cors_methods(methods: list[str]) -> list[str]:
    return _normalize_methods(methods) if methods else []


def _normalize_headers(headers: list[str]) -> list[str]:
    """Header names, checked; duplicates (in any case) dropped, first spelling kept."""
    seen: dict[str, str] = {}
    for header in headers:
        name = header.strip()
        if not _HEADER_NAME.fullmatch(name) or len(name) > 100:
            raise ValueError(f"{header!r} is not a header name")
        seen.setdefault(name.lower(), name)
    return list(seen.values())


class Cors(BaseModel):
    """A route's CORS settings: off while `origins` is empty."""

    model_config = ConfigDict(from_attributes=True)

    origins: Annotated[
        list[str],
        Field(max_length=MAX_CORS_ENTRIES, examples=[["https://app.example.com"]]),
        AfterValidator(_normalize_origins),
    ] = []
    methods: Annotated[
        list[str],
        Field(
            max_length=len(HTTP_METHODS),
            description="Methods a browser may use; empty: the route's own methods",
        ),
        AfterValidator(_normalize_cors_methods),
    ] = []
    headers: Annotated[
        list[str],
        Field(
            max_length=MAX_CORS_ENTRIES,
            description="Request headers a browser may send",
            examples=[["Content-Type", "X-API-Key"]],
        ),
        AfterValidator(_normalize_headers),
    ] = []
    credentials: bool = Field(
        default=False, description="Allow cookies and authorization headers (not with '*')"
    )
    max_age: int = Field(
        default=DEFAULT_CORS_MAX_AGE,
        ge=0,
        le=MAX_CORS_MAX_AGE,
        description="Seconds a browser may cache a preflight answer",
    )

    @model_validator(mode="after")
    def _no_wildcard_with_credentials(self) -> Self:
        # Browsers refuse `Access-Control-Allow-Origin: *` on a credentialed call.
        if self.credentials and "*" in self.origins:
            raise ValueError("credentials can't be allowed for '*' (any origin)")
        return self

    def columns(self) -> dict[str, object]:
        """The route's columns these settings are stored in."""
        return {f"cors_{field}": value for field, value in self.model_dump().items()}


class RouteCreate(BaseModel):
    service_id: uuid.UUID
    path_prefix: PathPrefix
    methods: Methods
    strip_prefix: bool = Field(
        default=False, description="Remove path_prefix before forwarding to the service"
    )
    auth_required: bool = Field(default=True, description="Require a valid X-API-Key")
    is_active: bool = True
    max_body_bytes: MaxBodyBytes | None = None
    cors: Cors = Cors()


class RouteUpdate(PartialUpdate):
    """`max_body_bytes: null` puts the route back on the platform default;
    `cors` replaces the route's CORS settings as a whole."""

    nullable = frozenset({"max_body_bytes"})

    service_id: uuid.UUID | None = None
    path_prefix: PathPrefix | None = None
    methods: Methods | None = None
    strip_prefix: bool | None = None
    auth_required: bool | None = None
    is_active: bool | None = None
    max_body_bytes: MaxBodyBytes | None = None
    cors: Cors | None = None


class RouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    service_id: uuid.UUID
    path_prefix: str
    methods: list[str]
    strip_prefix: bool
    auth_required: bool
    is_active: bool
    max_body_bytes: int | None  # the route's own limit; null: the platform default
    cors: Cors
    created_at: datetime
    updated_at: datetime
