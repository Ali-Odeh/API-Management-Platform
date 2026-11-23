"""Request and response shapes for /api/v1/workspaces/{slug}/apis."""

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, ClassVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.apis.models import DESCRIPTION_MAX_LENGTH, NAME_MAX_LENGTH, OPENAPI_MAX_BYTES, ApiStatus
from app.core.schemas import PartialUpdate, SlugName

MAX_ROUTES_PER_API = 1000


def compact_size(document: dict[str, Any]) -> int:
    """The document's size as JSON without spaces, in bytes."""
    return len(json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode())


def _check_openapi(document: dict[str, Any]) -> dict[str, Any]:
    """A document that says which OpenAPI (or Swagger 2.0) it is and has an `info`
    object, within the size limit. Not a full validation: the portal shows it as
    it is, and a broken one only hurts its own docs page."""
    version = document.get("openapi")
    if not (isinstance(version, str) and version.startswith("3.")) and (
        document.get("swagger") != "2.0"
    ):
        raise ValueError('an OpenAPI document needs "openapi": "3.x" (or "swagger": "2.0")')
    if not isinstance(document.get("info"), dict):
        raise ValueError('an OpenAPI document needs an "info" object')
    if compact_size(document) > OPENAPI_MAX_BYTES:
        raise ValueError(f"the OpenAPI document is larger than {OPENAPI_MAX_BYTES} bytes")
    return document


def _distinct(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if len(set(ids)) != len(ids):
        raise ValueError("route_ids must not repeat")
    return ids


OpenApiDocument = Annotated[
    dict[str, Any],
    AfterValidator(_check_openapi),
    Field(
        description=f"An OpenAPI 3.x (or Swagger 2.0) document as JSON, at most "
        f"{OPENAPI_MAX_BYTES} bytes",
        examples=[{"openapi": "3.1.0", "info": {"title": "Orders", "version": "1"}, "paths": {}}],
    ),
]
ApiName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=NAME_MAX_LENGTH,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
    Field(examples=["Orders API"]),
]
Description = Annotated[str, StringConstraints(max_length=DESCRIPTION_MAX_LENGTH)]
RouteIds = Annotated[
    list[uuid.UUID],
    Field(max_length=MAX_ROUTES_PER_API, description="The routes the API is made of"),
    AfterValidator(_distinct),
]


class ApiCreate(BaseModel):
    """A new API starts as a draft: only the workspace's members see it."""

    name: ApiName
    slug: SlugName
    description: Description = ""
    openapi: OpenApiDocument | None = None
    route_ids: RouteIds = []


class ApiUpdate(PartialUpdate):
    """`route_ids` replaces the API's routes as a whole; `openapi: null` removes the
    document. `status` moves along the lifecycle: draft -> published, published ->
    deprecated, deprecated -> published. The slug never changes."""

    nullable: ClassVar[frozenset[str]] = frozenset({"openapi"})

    name: ApiName | None = None
    description: Description | None = None
    openapi: OpenApiDocument | None = None
    route_ids: RouteIds | None = None
    status: ApiStatus | None = None


class ApiRouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    path_prefix: str
    methods: list[str]
    auth_required: bool


class ApiSummary(BaseModel):
    """An API in a list: everything but its (possibly large) document."""

    id: uuid.UUID
    name: str
    slug: str
    description: str
    status: ApiStatus
    has_openapi: bool
    routes: list[ApiRouteOut]
    created_at: datetime
    updated_at: datetime


class ApiOut(ApiSummary):
    openapi: dict[str, Any] | None
