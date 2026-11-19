"""Mock backend: echoes every request back as JSON.

It gives the gateway something to proxy to. The response shows exactly
what arrived (method, path, query, headers, body), so we can see what the
gateway changed, e.g. a stripped prefix or an added X-Request-ID.

?delay_ms=N makes it answer N milliseconds late, to see the gateway's
504 gateway_timeout without a real slow service.
"""

import asyncio
from typing import Annotated

from fastapi import FastAPI, Query, Request

MAX_DELAY_MS = 30_000

app = FastAPI(title="mock-backend")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def echo(
    path: str,
    request: Request,
    delay_ms: Annotated[int, Query(ge=0, le=MAX_DELAY_MS)] = 0,
) -> dict:
    if delay_ms:
        await asyncio.sleep(delay_ms / 1000)
    body = await request.body()
    return {
        "service": "mock-backend",
        "method": request.method,
        "path": "/" + path,
        "query": dict(request.query_params),
        "headers": dict(request.headers),
        "body": body.decode("utf-8", errors="replace"),
    }
