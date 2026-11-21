"""One structured log line per request, and the request's ids on every line.

Like the gateway's request log (PLAN.md section 7): JSON, one object per
line, with `request_id` and `workspace_id` (decision 17), so one change can
be followed through both services' logs. The query string is never logged
(it can hold tokens). GET /health isn't logged: Docker asks every 5 s.

The workspace is known only once the permission check has resolved the
slug, deep inside the request. So the request carries a small mutable
"notes" dict in a context variable: the check writes the workspace into it
(bind_workspace), and the request line and any log call made meanwhile
read it. A dict, not a value per field: code that runs in a copy of the
context (a thread pool) still writes into the same notes.
"""

import json
import logging
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.request")
_notes: ContextVar[dict[str, str] | None] = ContextVar("request_notes", default=None)
_NOT_LOGGED = frozenset({"/health"})


def bind_workspace(workspace_id: object) -> None:
    """Record the workspace this request acts on (no-op outside a request)."""
    notes = _notes.get()
    if notes is not None:
        notes["workspace_id"] = str(workspace_id)


class RequestLogMiddleware:
    """Pure ASGI, like RequestIdMiddleware (which runs before it): nothing is
    buffered, so the long-held /internal/config requests pass through."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        notes = {"request_id": scope.get("state", {}).get("request_id", ""), "workspace_id": ""}
        token = _notes.set(notes)
        status = 500  # unless the app starts an answer
        start = time.perf_counter()

        async def send_and_note(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_and_note)
        finally:
            if scope["path"] not in _NOT_LOGGED:
                logger.info(
                    "request",
                    extra={
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status,
                        "latency_ms": round((time.perf_counter() - start) * 1000, 3),
                    },
                )
            _notes.reset(token)


class _RequestIds(logging.Filter):
    """Adds the request's ids to every record ("" outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        notes = _notes.get() or {}
        record.request_id = notes.get("request_id", "")
        record.workspace_id = notes.get("workspace_id", "")
        return True


# Attributes every LogRecord has; anything else came in through `extra`.
_STANDARD = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """{"ts": ..., "level": ..., "logger": ..., "msg": ..., "request_id": ..., ...}"""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        line |= {k: v for k, v in record.__dict__.items() if k not in _STANDARD}
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging() -> None:
    """JSON lines on stdout for the app's loggers, with the request's ids.

    Called at startup (main.lifespan). uvicorn's own access log is off
    (--no-access-log): the request line above replaces it.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_RequestIds())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    # httpx logs every call at INFO with its full URL; GoTrue lookups carry
    # an email in the query string, which must not reach the log.
    for chatty in ("httpx", "httpcore"):
        logging.getLogger(chatty).setLevel(logging.WARNING)
