"""Mock backend: echoes every request back as JSON.

It gives the gateway something to proxy to. The response shows exactly
what arrived (method, path, query, headers, body), so we can see what the
gateway changed, e.g. a stripped prefix or an added X-Request-ID.
"""

from fastapi import FastAPI, Request

app = FastAPI(title="mock-backend")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def echo(path: str, request: Request) -> dict:
    body = await request.body()
    return {
        "service": "mock-backend",
        "method": request.method,
        "path": "/" + path,
        "query": dict(request.query_params),
        "headers": dict(request.headers),
        "body": body.decode("utf-8", errors="replace"),
    }
