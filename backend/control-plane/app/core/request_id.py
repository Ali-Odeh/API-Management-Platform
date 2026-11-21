"""A request ID for every call: the client's X-Request-ID when it is safe,
else a new one. Audit events record it, and the response echoes it, so one
admin change can be followed from the client to its audit entry.

Same rules as the gateway (1-128 characters of A-Z a-z 0-9 . _ -): anything
else could smuggle odd characters into logs and events.
"""

import re
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Request-ID"
_SAFE = re.compile(r"[A-Za-z0-9._-]{1,128}")


class RequestIdMiddleware:
    """Pure ASGI (no BaseHTTPMiddleware): nothing is buffered, and the
    long-held /internal/config requests pass through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        sent = Headers(scope=scope).get(HEADER)
        request_id = sent if sent is not None and _SAFE.fullmatch(sent) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)
