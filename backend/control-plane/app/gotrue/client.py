"""A thin client for GoTrue's admin API: create, invite and ban users.

The control plane never logs anyone in. It only asks GoTrue, with the
service-role key, to manage accounts. Everything else (passwords, MFA,
sessions) stays inside GoTrue.

`GoTrueAdmin` is the interface the rest of the app depends on; tests swap
in an in-memory fake, so unit tests never need a GoTrue process.
"""

import uuid
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.core.errors import UpstreamError

# "Banned until about a hundred years from now": GoTrue's way to block an account.
BAN_FOREVER = "876000h"
UNBAN = "none"
_TIMEOUT_SECONDS = 5.0
_UNAVAILABLE = "Auth service unavailable"


class GoTrueAdmin(Protocol):
    async def create_user(self, email: str, password: str) -> uuid.UUID | None:
        """Create a confirmed account with a password; None if the email is taken."""
        ...

    async def invite_user(self, email: str) -> uuid.UUID | None:
        """Create an account and email an invitation; None if the email is taken."""
        ...

    async def set_banned(self, auth_user_id: uuid.UUID, *, banned: bool) -> None:
        """Ban (no login, no refresh) or unban an account."""
        ...

    async def aclose(self) -> None: ...


class GoTrueAdminClient:
    """GoTrueAdmin over HTTP. One pooled httpx client for the app's lifetime."""

    def __init__(
        self,
        base_url: str,
        service_role_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {service_role_key}"},
            timeout=_TIMEOUT_SECONDS,
            transport=transport,
        )

    async def create_user(self, email: str, password: str) -> uuid.UUID | None:
        body = {"email": email, "password": password, "email_confirm": True}
        return await self._create("/admin/users", body)

    async def invite_user(self, email: str) -> uuid.UUID | None:
        return await self._create("/invite", {"email": email})

    async def set_banned(self, auth_user_id: uuid.UUID, *, banned: bool) -> None:
        body = {"ban_duration": BAN_FOREVER if banned else UNBAN}
        response = await self._send("PUT", f"/admin/users/{auth_user_id}", body)
        # 404: the account is gone from GoTrue, so it can't log in anyway.
        if response.status_code not in (200, 404):
            raise UpstreamError(_UNAVAILABLE)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _create(self, path: str, body: dict[str, Any]) -> uuid.UUID | None:
        response = await self._send("POST", path, body)
        if response.status_code == 200:
            return uuid.UUID(response.json()["id"])
        if response.status_code == 422 and _error_code(response) == "email_exists":
            return None
        raise UpstreamError(_UNAVAILABLE)

    async def _send(self, method: str, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self._http.request(method, path, json=body)
        except httpx.HTTPError as exc:  # refused, timed out, reset, ...
            raise UpstreamError(_UNAVAILABLE) from exc


def _error_code(response: httpx.Response) -> str | None:
    try:
        code = response.json().get("error_code")
    except ValueError:  # not JSON
        return None
    return code if isinstance(code, str) else None


def create_gotrue_admin(settings: Settings) -> GoTrueAdmin:
    """The real client, built from settings. Tests replace this function."""
    return GoTrueAdminClient(settings.gotrue_url, settings.gotrue_service_role_key)
