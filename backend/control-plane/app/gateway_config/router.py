"""GET /internal/config: the gateway's only door into the control plane."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.core.deps import InternalOnly
from app.core.openapi import error_responses
from app.gateway_config.provider import ConfigProvider
from app.gateway_config.schemas import ConfigAnswer

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[InternalOnly])

MAX_WAIT_SECONDS = 60


def get_config_provider(request: Request) -> ConfigProvider:
    """Created once at startup (see main.py lifespan) and shared by all requests."""
    provider: ConfigProvider = request.app.state.config_provider
    return provider


def _slugs(workspaces: str | None) -> frozenset[str] | None:
    """`acme,globex` -> {"acme", "globex"}; nothing (or only commas) -> no filter."""
    names = frozenset(s.strip() for s in (workspaces or "").split(",") if s.strip())
    return names or None


@router.get(
    "/config",
    response_model=ConfigAnswer,
    responses={
        **error_responses({401: "Missing or wrong X-Internal-Token"}),
        304: {"description": "Not modified: nothing changed after `since`"},
    },
)
async def get_config(
    provider: Annotated[ConfigProvider, Depends(get_config_provider)],
    response: Response,
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
) -> ConfigAnswer | Response:
    """The workspaces that changed after `since`, or 304 if none did.

    Each changed workspace comes back complete; deleted ones by id. A full
    answer (`full: true`, every workspace) for `since=0`, or a `since` newer
    than the database's sequence (e.g. after a reset). With `?wait=30` and
    nothing new, the request is held open and answered the moment the config
    changes (Postgres NOTIFY), or with 304 after 30 seconds.
    """
    answer = await provider.get_changes(since, wait, _slugs(workspaces))
    if answer is None:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": f'"{since}"'})
    response.headers["ETag"] = f'"{answer.seq}"'
    response.headers["Cache-Control"] = "no-cache"  # always ask us, never trust a cache
    return answer
