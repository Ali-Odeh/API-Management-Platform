"""The control plane's JSON log lines, with request_id and workspace_id (MT.7)."""

import io
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient
from starlette.types import Receive, Scope, Send

from app.core.request_id import RequestIdMiddleware
from app.core.request_log import (
    JsonFormatter,
    RequestLogMiddleware,
    bind_workspace,
    configure_logging,
)
from app.workspaces.models import Workspace

Line = dict[str, Any]


@contextmanager
def captured_lines() -> Iterator[list[Line]]:
    """Logging as configured in production, into a buffer: the JSON lines
    written inside the block, parsed, once it ends."""
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    quiet = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    configure_logging()
    stream = io.StringIO()
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(stream)
    lines: list[Line] = []
    try:
        yield lines
    finally:
        root.handlers, root.level = saved
        for name, level in quiet.items():
            logging.getLogger(name).setLevel(level)
        lines.extend(json.loads(line) for line in stream.getvalue().splitlines())


def _requests(lines: list[Line]) -> list[Line]:
    return [line for line in lines if line["msg"] == "request"]


async def test_a_workspace_request_is_logged_with_its_ids(
    client: AsyncClient, auth_headers: dict[str, str], workspace: Workspace
) -> None:
    with captured_lines() as lines:
        r = await client.get(
            "/api/v1/workspaces/default/services?page=1&secret=x", headers=auth_headers
        )
        await client.get("/health")

    [line] = _requests(lines)  # /health isn't logged
    assert line["request_id"] == r.headers["X-Request-ID"]
    assert line["workspace_id"] == str(workspace.id)
    assert (line["method"], line["path"], line["status"]) == (
        "GET", "/api/v1/workspaces/default/services", 200,
    )  # fmt: skip
    assert line["latency_ms"] >= 0
    # Never a query string: not in the request line, and not from httpx,
    # which would log GoTrue lookups by email.
    assert "secret" not in json.dumps(lines)


async def test_a_platform_request_names_the_workspace_it_acts_on(
    client: AsyncClient, mfa_headers: dict[str, str], workspace: Workspace
) -> None:
    with captured_lines() as lines:
        r = await client.post(
            "/api/v1/workspaces/default/suspend", json={"reason": "x"}, headers=mfa_headers
        )

    assert r.status_code == 409  # `default` is never suspended, and the line still names it
    [line] = _requests(lines)
    assert line["workspace_id"] == str(workspace.id)


# ---- The middleware and the filter on their own, around tiny ASGI apps


async def _binds_and_logs(scope: Scope, receive: Receive, send: Send) -> None:
    bind_workspace("w-1")
    logging.getLogger("app.somewhere").warning("something happened")
    await send({"type": "http.response.start", "status": 201, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _crashes(scope: Scope, receive: Receive, send: Send) -> None:
    raise RuntimeError("boom")


async def _call(app: Any) -> None:
    wrapped = RequestIdMiddleware(RequestLogMiddleware(app))
    transport = ASGITransport(app=wrapped, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        await client.get("/x", headers={"X-Request-ID": "req-1"})


async def test_every_line_of_a_request_carries_its_ids() -> None:
    with captured_lines() as lines:
        await _call(_binds_and_logs)
        await _call(_crashes)
        logging.getLogger("app.outside").info("no request here")

    warning = next(line for line in lines if line["msg"] == "something happened")
    assert (warning["request_id"], warning["workspace_id"]) == ("req-1", "w-1")
    assert (warning["level"], warning["logger"]) == ("WARNING", "app.somewhere")
    ok, crashed = _requests(lines)
    assert (ok["status"], ok["workspace_id"]) == (201, "w-1")
    assert (crashed["status"], crashed["workspace_id"]) == (500, "")  # a crash is still logged
    outside = next(line for line in lines if line["msg"] == "no request here")
    assert (outside["request_id"], outside["workspace_id"]) == ("", "")


async def test_other_scopes_pass_through() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> dict[str, Any]:
        return {}

    async def send(message: Any) -> None:
        return None

    await RequestLogMiddleware(app)({"type": "lifespan"}, receive, send)
    assert seen == ["lifespan"]


def test_binding_outside_a_request_does_nothing() -> None:
    bind_workspace("w-1")  # no request notes to write into, and no error


def test_the_formatter_adds_the_exception() -> None:
    try:
        raise ValueError("bad")
    except ValueError:
        record = logging.getLogger("t").makeRecord(
            "t", logging.ERROR, __file__, 1, "failed", None, exc_info=sys.exc_info()
        )
    line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "failed"
    assert "ValueError: bad" in line["exc"]
