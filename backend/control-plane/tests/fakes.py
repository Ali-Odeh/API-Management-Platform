"""Stand-ins for outside services, so unit tests never need a GoTrue process.

gotrue = FakeGoTrue()
token = make_token(user.auth_user_id, user.email, aal="aal2")
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.core.identity import JWT_ALGORITHM, JWT_AUDIENCE


def make_token(
    auth_user_id: uuid.UUID | str,
    email: str,
    /,
    *,
    aal: str = "aal1",
    expires_in: timedelta = timedelta(minutes=10),
    secret: str | None = None,
    **claims: Any,
) -> str:
    """An access token shaped like GoTrue's, signed with the test GoTrue secret.

    Extra keyword arguments add or override claims, even `sub` and `email`
    (`aud=None` removes one).
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(auth_user_id),
        "aud": JWT_AUDIENCE,
        "email": email,
        "aal": aal,
        "role": "",
        "iat": now,
        "exp": now + expires_in,
        **claims,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, secret or get_settings().gotrue_jwt_secret, JWT_ALGORITHM)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeGoTrue:
    """GoTrue's admin API in memory. Records what the app asked for."""

    def __init__(self) -> None:
        self.accounts: dict[str, uuid.UUID] = {}  # email -> GoTrue user id
        self.passwords: dict[str, str] = {}
        self.invited: list[str] = []
        self.banned: set[uuid.UUID] = set()
        self.ban_calls: list[tuple[uuid.UUID, bool]] = []
        self.failing = False  # True: every call fails like an unreachable GoTrue
        self.lookup_failing = False  # True: only find_user_by_email fails
        self.account_missing_on_ban = False  # True: account disappeared after lookup
        # True: set_banned acts, then fails, like a timeout after GoTrue applied it.
        self.fail_after_ban = False
        self.ban_delay = 0.0  # seconds set_banned takes, so parallel calls overlap
        self.closed = False

    def add_account(self, email: str) -> uuid.UUID:
        """An account that already exists in GoTrue (made outside the app)."""
        self.accounts[email] = uuid.uuid4()
        return self.accounts[email]

    async def find_user_by_email(self, email: str) -> uuid.UUID | None:
        self._maybe_fail()
        if self.lookup_failing:
            raise UpstreamError("Auth service unavailable")
        return self.accounts.get(email)

    async def create_user(self, email: str, password: str) -> uuid.UUID | None:
        self._maybe_fail()
        if email in self.accounts:
            return None
        self.passwords[email] = password
        return self.add_account(email)

    async def invite_user(self, email: str) -> uuid.UUID | None:
        self._maybe_fail()
        if email in self.accounts:
            return None
        self.invited.append(email)
        return self.add_account(email)

    async def set_banned(self, auth_user_id: uuid.UUID, *, banned: bool) -> bool:
        self._maybe_fail()
        await asyncio.sleep(self.ban_delay)
        self.ban_calls.append((auth_user_id, banned))
        if self.account_missing_on_ban:
            return False
        if banned:
            self.banned.add(auth_user_id)
        else:
            self.banned.discard(auth_user_id)
        if self.fail_after_ban:
            raise UpstreamError("Auth service unavailable")
        return True

    async def aclose(self) -> None:
        self.closed = True

    def _maybe_fail(self) -> None:
        if self.failing:
            raise UpstreamError("Auth service unavailable")
