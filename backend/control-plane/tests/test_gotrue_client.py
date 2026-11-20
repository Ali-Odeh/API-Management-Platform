"""The real GoTrue admin client, against GoTrue's answers played by httpx.MockTransport.

The answers mirror GoTrue v2.197.0 (checked by hand against the Compose
service); the e2e tests then run the same calls against the real thing.
"""

import json
import uuid

import httpx
import pytest

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.gotrue.client import GoTrueAdminClient, create_gotrue_admin

NEW_ID = uuid.uuid4()
EMAIL_EXISTS = httpx.Response(
    422, json={"code": 422, "error_code": "email_exists", "msg": "already registered"}
)


class Recorder:
    """A fake GoTrue that answers with one response and remembers the requests."""

    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def last_json(self) -> object:
        return json.loads(self.last.content)


def _client(recorder: Recorder) -> GoTrueAdminClient:
    return GoTrueAdminClient(
        "http://auth:9999", "service-role-key", transport=httpx.MockTransport(recorder)
    )


async def test_create_user_sends_a_confirmed_account() -> None:
    gotrue = Recorder(httpx.Response(200, json={"id": str(NEW_ID), "email": "a@example.com"}))

    assert await _client(gotrue).create_user("a@example.com", "a-long-password") == NEW_ID

    assert gotrue.last.method == "POST"
    assert str(gotrue.last.url) == "http://auth:9999/admin/users"
    assert gotrue.last.headers["Authorization"] == "Bearer service-role-key"
    assert gotrue.last_json() == {
        "email": "a@example.com",
        "password": "a-long-password",
        "email_confirm": True,
    }


async def test_invite_user() -> None:
    gotrue = Recorder(httpx.Response(200, json={"id": str(NEW_ID)}))

    assert await _client(gotrue).invite_user("a@example.com") == NEW_ID

    assert str(gotrue.last.url) == "http://auth:9999/invite"
    assert gotrue.last_json() == {"email": "a@example.com"}


@pytest.mark.parametrize("method", ["create", "invite"])
async def test_existing_email_gives_none(method: str) -> None:
    client = _client(Recorder(EMAIL_EXISTS))
    if method == "create":
        assert await client.create_user("a@example.com", "a-long-password") is None
    else:
        assert await client.invite_user("a@example.com") is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(422, json={"error_code": "weak_password"}),
        httpx.Response(422, text="not json"),
        httpx.Response(422, json={"error_code": 7}),
        httpx.Response(401, json={"msg": "invalid JWT"}),
        httpx.Response(500),
    ],
    ids=["weak password", "not json", "odd error code", "bad key", "server error"],
)
async def test_other_answers_raise(response: httpx.Response) -> None:
    with pytest.raises(UpstreamError):
        await _client(Recorder(response)).create_user("a@example.com", "short")


@pytest.mark.parametrize(("banned", "duration"), [(True, "876000h"), (False, "none")])
async def test_set_banned(banned: bool, duration: str) -> None:
    gotrue = Recorder(httpx.Response(200, json={"id": str(NEW_ID)}))

    await _client(gotrue).set_banned(NEW_ID, banned=banned)

    assert gotrue.last.method == "PUT"
    assert str(gotrue.last.url) == f"http://auth:9999/admin/users/{NEW_ID}"
    assert gotrue.last_json() == {"ban_duration": duration}


async def test_banning_an_account_gotrue_no_longer_has_is_fine() -> None:
    await _client(Recorder(httpx.Response(404))).set_banned(NEW_ID, banned=True)


async def test_set_banned_failure_raises() -> None:
    with pytest.raises(UpstreamError):
        await _client(Recorder(httpx.Response(500))).set_banned(NEW_ID, banned=True)


async def test_unreachable_gotrue_raises() -> None:
    gotrue = Recorder(httpx.ConnectError("connection refused"))
    with pytest.raises(UpstreamError, match="Auth service unavailable"):
        await _client(gotrue).invite_user("a@example.com")


async def test_factory_builds_the_client_from_settings() -> None:
    client = create_gotrue_admin(get_settings())
    assert isinstance(client, GoTrueAdminClient)
    await client.aclose()
