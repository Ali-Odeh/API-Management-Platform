"""GET /internal/config: the gateway's only door into the control plane."""

import json
import uuid
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.core.deps import InternalOnly
from app.core.openapi import error_responses
from app.gateway_config.provider import ConfigProvider
from app.gateway_config.schemas import ConfigAnswer

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[InternalOnly])

MAX_WAIT_SECONDS = 60
GENERATION_HEADER = "X-Config-Generation"


def get_config_provider(request: Request) -> ConfigProvider:
    """Created once at startup (see main.py lifespan) and shared by all requests."""
    provider: ConfigProvider = request.app.state.config_provider
    return provider


def _slugs(workspaces: str | None) -> frozenset[str] | None:
    """`acme,globex` -> {"acme", "globex"}; nothing (or only commas) -> no filter."""
    names = frozenset(s.strip() for s in (workspaces or "").split(",") if s.strip())
    return names or None


def _stream(answer: ConfigAnswer) -> Iterator[str]:
    """Send the same JSON envelope without serializing the whole snapshot at once."""
    metadata = json.dumps({"seq": answer.seq, "full": answer.full})
    yield metadata[:-1] + ',"workspaces":['
    for index, workspace in enumerate(answer.workspaces):
        if index:
            yield ","
        yield workspace.model_dump_json()
    yield '],"deleted":['
    for index, workspace_id in enumerate(answer.deleted):
        if index:
            yield ","
        yield json.dumps(str(workspace_id))
    yield "]}"


@router.get(
    "/config",
    response_model=ConfigAnswer,
    responses={
        **error_responses({401: "Missing or wrong X-Internal-Token"}),
        200: {"headers": {GENERATION_HEADER: {"schema": {"type": "string", "format": "uuid"}}}},
        304: {"description": "Not modified: nothing changed after `since`"},
    },
)
async def get_config(
    provider: Annotated[ConfigProvider, Depends(get_config_provider)],
    since: Annotated[
        int,
        Query(ge=0, description="The last `seq` the gateway applied; 0 for everything"),
    ] = 0,
    wait: Annotated[
        int,
        Query(
            ge=0,
            le=MAX_WAIT_SECONDS,
            description="Long polling: seconds to hold the request if nothing changed",
        ),
    ] = 0,
    workspaces: Annotated[
        str | None,
        Query(description="Only these workspaces (slugs, comma-separated): a dedicated gateway"),
    ] = None,
    generation: Annotated[
        uuid.UUID | None,
        Header(alias=GENERATION_HEADER, description="Database generation from the last config"),
    ] = None,
) -> Response:
    """The workspaces that changed after `since`, or 304 if none did.

    Each changed workspace comes back complete; deleted ones by id. A full
    answer (`full: true`, every workspace) for `since=0`, or a `since` newer
    than the database's sequence (e.g. after a reset). With `?wait=30` and
    nothing new, the request is held open and answered the moment the config
    changes (Postgres NOTIFY), or with 304 after 30 seconds.
    """
    result = await provider.get_changes(since, wait, _slugs(workspaces), generation=generation)
    if result is None:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": f'"{since}"'})
    cursor, answer = result
    return StreamingResponse(
        _stream(answer),
        media_type="application/json",
        headers={
            "ETag": f'"{answer.seq}"',
            "Cache-Control": "no-cache",
            GENERATION_HEADER: str(cursor.generation),
        },
    )
