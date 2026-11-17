"""GET /internal/config: the gateway's only door into the control plane."""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from app.core.deps import InternalOnly
from app.core.openapi import error_responses
from app.gateway_config.provider import ConfigProvider
from app.gateway_config.schemas import ConfigSnapshot

router = APIRouter(prefix="/internal", tags=["internal"], dependencies=[InternalOnly])

MAX_WAIT_SECONDS = 60
_ETAG = re.compile(r'^(?:W/)?"?(\d+)"?$')  # "46", W/"46" or 46


def get_config_provider(request: Request) -> ConfigProvider:
    """Created once at startup (see main.py lifespan) and shared by all requests."""
    provider: ConfigProvider = request.app.state.config_provider
    return provider


def _version_from_etag(if_none_match: str | None) -> int | None:
    match = _ETAG.match(if_none_match.strip()) if if_none_match else None
    return int(match.group(1)) if match else None


@router.get(
    "/config",
    response_model=ConfigSnapshot,
    responses={
        **error_responses({401: "Missing or wrong X-Internal-Token"}),
        304: {"description": "Not modified: the gateway's version is still current"},
    },
)
async def get_config(
    provider: Annotated[ConfigProvider, Depends(get_config_provider)],
    response: Response,
    if_none_match: Annotated[
        str | None, Header(description='The version the gateway has, e.g. "45"')
    ] = None,
    wait: Annotated[
        int,
        Query(
            ge=0,
            le=MAX_WAIT_SECONDS,
            description="Long polling: seconds to hold the request if nothing changed",
        ),
    ] = 0,
) -> ConfigSnapshot | Response:
    """The full config snapshot, or 304 if the gateway's version is current.

    With `?wait=30` and nothing new, the request is held open and answered the
    moment the config changes (Postgres NOTIFY), or with 304 after 30 seconds.
    """
    known_version = _version_from_etag(if_none_match)
    snapshot = await provider.get_if_changed(known_version, wait)
    if snapshot is None:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": f'"{known_version}"'}
        )
    response.headers["ETag"] = f'"{snapshot.version}"'
    response.headers["Cache-Control"] = "no-cache"  # always ask us, never trust a cache
    return snapshot
