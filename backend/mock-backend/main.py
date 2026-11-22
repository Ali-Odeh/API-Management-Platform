"""Mock backend: echoes every request back as JSON.

It gives the gateway something to proxy to. The response shows exactly
what arrived (method, path, query, headers, body), so we can see what the
gateway changed, e.g. a stripped prefix or an added X-Request-ID.

?delay_ms=N makes it answer N milliseconds late, to see the gateway's
504 gateway_timeout without a real slow service.

Load balancing (MT.10): the echo names the instance (INSTANCE_NAME, set by
Compose for each copy), so a test sees which target answered. A test can
make an instance act as if it were down with PUT /_mock/state
{"up": false}: its /health and every request then answer 503, until
{"up": true}. Only the test stack runs this service.
"""

import asyncio
import os
from typing import Annotated

from fastapi import FastAPI, Query, Request, Response, status
from pydantic import BaseModel

MAX_DELAY_MS = 30_000
INSTANCE_NAME = os.environ.get("INSTANCE_NAME", "mock-backend")

app = FastAPI(title="mock-backend")


class State(BaseModel):
    up: bool


_state = State(up=True)


@app.put("/_mock/state")
async def set_state(state: State) -> State:
    global _state
    _state = state
    return _state


@app.get("/health")
async def health(response: Response) -> dict:
    if not _state.up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "down", "service": INSTANCE_NAME}
    return {"status": "ok", "service": INSTANCE_NAME}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def echo(
    path: str,
    request: Request,
    response: Response,
    delay_ms: Annotated[int, Query(ge=0, le=MAX_DELAY_MS)] = 0,
) -> dict:
    if not _state.up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"error": "mock_down", "service": INSTANCE_NAME}
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)
    body = await request.body()
    return {
        "service": INSTANCE_NAME,
        "method": request.method,
        "path": "/" + path,
        "query": dict(request.query_params),
        "headers": dict(request.headers),
        "body": body.decode("utf-8", errors="replace"),
    }
