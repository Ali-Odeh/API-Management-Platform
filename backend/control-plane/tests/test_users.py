"""API tests for /api/v1/me and /api/v1/users: GoTrue tokens, users made just in time,
disabled users, and the Platform Admin's MFA rule."""

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.errors import ForbiddenError
from app.core.identity import TokenClaims
from app.users import crud as users_crud
from app.users.crud import DISABLED_NOT_BANNED
from app.users.models import User
from app.workspaces.models import Workspace
from tests.factories import make_user
from tests.fakes import FakeGoTrue, bearer, make_token

ME = "/api/v1/me"
USERS = "/api/v1/users"
SERVICES = "/api/v1/services"

Headers = dict[str, str]


async def _count_users(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User)) or 0


# ---- Who is calling: tokens and the just-in-time row


async def test_me_returns_the_caller(
    client: AsyncClient, admin: User, auth_headers: Headers
) -> None:
    r = await client.get(ME, headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {
        "id": str(admin.id),
        "email": admin.email,
        "is_platform_admin": True,
        "is_active": True,
        "created_at": admin.created_at.isoformat().replace("+00:00", "Z"),
    }


async def test_first_request_creates_the_user(client: AsyncClient, db: AsyncSession) -> None:
    auth_user_id = uuid.uuid4()
    token = make_token(auth_user_id, "New.Person@Example.COM")

    first = await client.get(ME, headers=bearer(token))
    again = await client.get(ME, headers=bearer(token))

    assert first.status_code == again.status_code == 200
    body = first.json()
    assert body["email"] == "new.person@example.com"  # stored lowercase
    assert body["is_platform_admin"] is False  # no rights by default
    assert again.json()["id"] == body["id"]
    user = await db.get(User, uuid.UUID(body["id"]))
    assert user is not None and user.auth_user_id == auth_user_id
    assert await _count_users(db) == 1


@pytest.mark.parametrize("linked", [True, False], ids=["linked row", "row not linked yet"])
async def test_another_account_never_inherits_a_row_by_email(
    client: AsyncClient, db: AsyncSession, linked: bool
) -> None:
    # A Platform Admin's row, and a different GoTrue account with the same email
    # (e.g. one GoTrue made again): it must not get the row or its rights.
    boss = await make_user(db, email="boss@example.com", linked=linked, is_platform_admin=True)
    stranger = bearer(make_token(uuid.uuid4(), "Boss@Example.com", aal="aal2"))

    for path in (ME, SERVICES):
        r = await client.get(path, headers=stranger)
        assert r.status_code == 403, path
        assert r.json() == {"detail": "account_conflict"}
    await db.refresh(boss)
    assert boss.auth_user_id is None or linked
    assert await _count_users(db) == 1


async def test_the_linked_account_keeps_working(client: AsyncClient, db: AsyncSession) -> None:
    boss = await make_user(db, email="boss@example.com", is_platform_admin=True)
    assert boss.auth_user_id is not None
    r = await client.get(ME, headers=bearer(make_token(boss.auth_user_id, boss.email)))
    assert r.status_code == 200 and r.json()["id"] == str(boss.id)


async def test_parallel_first_requests_make_one_user(client: AsyncClient, db: AsyncSession) -> None:
    token = make_token(uuid.uuid4(), "racer@example.com")
    results = await asyncio.gather(*(client.get(ME, headers=bearer(token)) for _ in range(5)))

    assert [r.status_code for r in results] == [200] * 5
    assert len({r.json()["id"] for r in results}) == 1
    assert await _count_users(db) == 1


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": "Basic YWRtaW46cGFzc3dvcmQ="},
        bearer(make_token(uuid.uuid4(), "x@example.com", secret="y" * 40)),
    ],
    ids=["missing", "garbage", "not bearer", "forged"],
)
async def test_without_a_valid_token_is_401(client: AsyncClient, headers: Headers) -> None:
    r = await client.get(ME, headers=headers)
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_disabled_user_is_refused_even_with_a_valid_token(
    client: AsyncClient, db: AsyncSession
) -> None:
    user = await make_user(db, is_active=False, is_platform_admin=True)
    assert user.auth_user_id is not None
    token = make_token(user.auth_user_id, user.email, aal="aal2")

    for path in (ME, SERVICES, f"{USERS}/{user.id}"):
        r = await client.request(
            "PATCH" if path.startswith(USERS) else "GET", path, json={}, headers=bearer(token)
        )
        assert r.status_code == 403, path
        assert r.json() == {"detail": "user_disabled"}


# ---- Workspace endpoints in MT.1: Platform Admins only, acting on `default`


async def test_workspace_endpoints_refuse_other_users(
    client: AsyncClient, db: AsyncSession
) -> None:
    person = await make_user(db)
    assert person.auth_user_id is not None
    r = await client.get(SERVICES, headers=bearer(make_token(person.auth_user_id, person.email)))
    assert r.status_code == 403
    assert r.json() == {"detail": "platform_admin_required"}


async def test_workspace_endpoints_need_the_default_workspace(
    client: AsyncClient, db: AsyncSession
) -> None:
    # Startup creates it; without it there is nothing to act on.
    admin = await make_user(db, is_platform_admin=True)
    assert admin.auth_user_id is not None
    r = await client.get(SERVICES, headers=bearer(make_token(admin.auth_user_id, admin.email)))
    assert r.status_code == 404


async def test_workspace_endpoints_work_without_mfa(
    client: AsyncClient, auth_headers: Headers, workspace: Workspace
) -> None:
    r = await client.get(SERVICES, headers=auth_headers)
    assert r.status_code == 200


# ---- PATCH /api/v1/users/{id}: disable and enable (Platform Admin with MFA)


@pytest.fixture
async def person(db: AsyncSession) -> User:
    return await make_user(db, email="dev@example.com")


def _person_headers(person: User) -> Headers:
    assert person.auth_user_id is not None
    return bearer(make_token(person.auth_user_id, person.email))


async def test_disable_locks_the_person_out_at_once(
    client: AsyncClient, mfa_headers: Headers, person: User, gotrue: FakeGoTrue
) -> None:
    assert (await client.get(ME, headers=_person_headers(person))).status_code == 200

    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": False}, headers=mfa_headers)

    assert r.status_code == 200
    assert r.json()["is_active"] is False
    assert person.auth_user_id in gotrue.banned  # no login, no refresh
    # The same, still unexpired token stops working on the very next request.
    after = await client.get(ME, headers=_person_headers(person))
    assert after.status_code == 403
    assert after.json() == {"detail": "user_disabled"}


async def test_enable_lets_the_person_back_in(
    client: AsyncClient, mfa_headers: Headers, person: User, gotrue: FakeGoTrue
) -> None:
    await client.patch(f"{USERS}/{person.id}", json={"is_active": False}, headers=mfa_headers)
    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": True}, headers=mfa_headers)

    assert r.status_code == 200 and r.json()["is_active"] is True
    assert person.auth_user_id not in gotrue.banned
    assert (await client.get(ME, headers=_person_headers(person))).status_code == 200


async def test_platform_endpoint_needs_mfa(
    client: AsyncClient, auth_headers: Headers, person: User, gotrue: FakeGoTrue
) -> None:
    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": False}, headers=auth_headers)
    assert r.status_code == 403
    assert r.json() == {"detail": "mfa_required"}
    assert gotrue.ban_calls == []


async def test_platform_endpoint_refuses_other_users_even_with_mfa(
    client: AsyncClient, db: AsyncSession, person: User
) -> None:
    other = await make_user(db, email="other@example.com")
    assert other.auth_user_id is not None
    token = make_token(other.auth_user_id, other.email, aal="aal2")

    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": False}, headers=bearer(token))

    assert r.status_code == 403
    assert r.json() == {"detail": "platform_admin_required"}


async def test_admin_cannot_disable_themselves(
    client: AsyncClient, admin: User, mfa_headers: Headers
) -> None:
    r = await client.patch(f"{USERS}/{admin.id}", json={"is_active": False}, headers=mfa_headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "You can't disable yourself"}


async def test_unknown_user_is_404(client: AsyncClient, mfa_headers: Headers) -> None:
    r = await client.patch(
        f"{USERS}/{uuid.uuid4()}", json={"is_active": False}, headers=mfa_headers
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "User not found"}


@pytest.mark.parametrize("body", [{"is_active": None}, {"is_active": "maybe"}])
async def test_invalid_body_is_422(
    client: AsyncClient, mfa_headers: Headers, person: User, body: dict[str, object]
) -> None:
    r = await client.patch(f"{USERS}/{person.id}", json=body, headers=mfa_headers)
    assert r.status_code == 422


async def test_empty_body_changes_nothing(
    client: AsyncClient, mfa_headers: Headers, person: User, gotrue: FakeGoTrue
) -> None:
    r = await client.patch(f"{USERS}/{person.id}", json={}, headers=mfa_headers)
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert gotrue.ban_calls == []


async def test_gotrue_down_keeps_a_disable(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers, person: User, gotrue: FakeGoTrue
) -> None:
    gotrue.failing = True
    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": False}, headers=mfa_headers)

    assert r.status_code == 502
    assert r.json() == {"detail": DISABLED_NOT_BANNED}
    await db.refresh(person)
    assert person.is_active is False  # the safe side: the API is closed already
    assert (await client.get(ME, headers=_person_headers(person))).status_code == 403

    # Repeating the request once GoTrue is back finishes the ban.
    gotrue.failing = False
    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": False}, headers=mfa_headers)
    assert r.status_code == 200
    assert person.auth_user_id in gotrue.banned


async def test_gotrue_down_makes_no_enable(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers, gotrue: FakeGoTrue
) -> None:
    person = await make_user(db, email="dev@example.com", is_active=False)
    gotrue.failing = True

    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": True}, headers=mfa_headers)

    assert r.status_code == 502
    assert r.json() == {"detail": "Auth service unavailable"}
    await db.refresh(person)
    assert person.is_active is False


@pytest.mark.parametrize("enable", [False, True], ids=["disable", "enable"])
async def test_timeout_after_gotrue_acted(
    client: AsyncClient,
    db: AsyncSession,
    mfa_headers: Headers,
    gotrue: FakeGoTrue,
    enable: bool,
) -> None:
    # GoTrue applied the change but the answer never came back: the row must
    # stay on the safe side, and a retry must end with both in step.
    person = await make_user(db, email="dev@example.com", is_active=not enable)
    assert person.auth_user_id is not None
    if enable:
        gotrue.banned.add(person.auth_user_id)
    gotrue.fail_after_ban = True

    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": enable}, headers=mfa_headers)

    assert r.status_code == 502
    await db.refresh(person)
    assert person.is_active is False  # disable kept; enable not made
    assert (await client.get(ME, headers=_person_headers(person))).status_code == 403

    gotrue.fail_after_ban = False
    r = await client.patch(f"{USERS}/{person.id}", json={"is_active": enable}, headers=mfa_headers)
    assert r.status_code == 200 and r.json()["is_active"] is enable
    assert (person.auth_user_id in gotrue.banned) is (not enable)


async def test_parallel_changes_end_with_row_and_gotrue_in_step(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers, person: User, gotrue: FakeGoTrue
) -> None:
    # Each request holds the row's lock across its GoTrue call, so whatever
    # order they run in, the last one wins in both places.
    gotrue.ban_delay = 0.02
    for _ in range(5):
        bodies = [{"is_active": False}, {"is_active": True}, {"is_active": False}]
        results = await asyncio.gather(
            *(client.patch(f"{USERS}/{person.id}", json=b, headers=mfa_headers) for b in bodies)
        )
        assert [r.status_code for r in results] == [200] * 3
        await db.refresh(person)
        last_ban = gotrue.ban_calls[-1]
        assert last_ban == (person.auth_user_id, not person.is_active)
        assert (person.auth_user_id in gotrue.banned) is (not person.is_active)


async def test_unlinked_account_is_found_and_banned(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers, gotrue: FakeGoTrue
) -> None:
    # A GoTrue account exists, but its row was never linked (no login yet):
    # the trusted admin API finds it by exact email, so refresh is blocked too.
    waiting = await make_user(db, email="later@example.com", linked=False)
    account = gotrue.add_account("later@example.com")

    r = await client.patch(f"{USERS}/{waiting.id}", json={"is_active": False}, headers=mfa_headers)

    assert r.status_code == 200 and r.json()["is_active"] is False
    assert account in gotrue.banned
    await db.refresh(waiting)
    assert waiting.auth_user_id == account


async def test_user_without_a_gotrue_account_is_disabled_here_only(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers, gotrue: FakeGoTrue
) -> None:
    # No GoTrue account at all yet: nothing to ban; the row refuses them anyway.
    waiting = await make_user(db, email="later@example.com", linked=False)

    r = await client.patch(f"{USERS}/{waiting.id}", json={"is_active": False}, headers=mfa_headers)

    assert r.status_code == 200 and r.json()["is_active"] is False
    assert gotrue.ban_calls == []


# ---- The race on a first request, made deterministic


async def _first_request_while_another_saves(
    monkeypatch: pytest.MonkeyPatch, claims: TokenClaims, competitor: User
) -> User:
    """Run get_or_create_from_token while `competitor` is saved by another
    request between our lookups and our commit (the window a race hits)."""
    original = users_crud._by_email

    async def lookup_then_lose_the_race(db: AsyncSession, email: str) -> User | None:
        found = await original(db, email)
        async with SessionLocal() as other:
            other.add(competitor)
            await other.commit()
        return found

    monkeypatch.setattr(users_crud, "_by_email", lookup_then_lose_the_race)
    async with SessionLocal() as db:
        return await users_crud.get_or_create_from_token(db, claims)


async def test_losing_the_race_returns_the_other_requests_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = TokenClaims(auth_user_id=uuid.uuid4(), email="racer@example.com", aal="aal1")
    competitor = User(email="racer@example.com", auth_user_id=claims.auth_user_id)

    user = await _first_request_while_another_saves(monkeypatch, claims, competitor)

    assert user.id == competitor.id


async def test_losing_the_race_to_another_account_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Another GoTrue account took the email in the meantime: refused like any
    # other account conflict, never linked.
    claims = TokenClaims(auth_user_id=uuid.uuid4(), email="racer@example.com", aal="aal1")
    competitor = User(email="racer@example.com", auth_user_id=uuid.uuid4())

    with pytest.raises(ForbiddenError, match="account_conflict"):
        await _first_request_while_another_saves(monkeypatch, claims, competitor)


async def test_row_made_between_our_two_lookups_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The parallel request commits after our lookup by id but before the one by
    # email: the email's row is bound to this very account, so it is ours.
    claims = TokenClaims(auth_user_id=uuid.uuid4(), email="racer@example.com", aal="aal1")
    competitor = User(email="racer@example.com", auth_user_id=claims.auth_user_id)
    original = users_crud._by_auth_user_id

    async def miss_then_lose_the_race(db: AsyncSession, auth_user_id: uuid.UUID) -> User | None:
        found = await original(db, auth_user_id)
        async with SessionLocal() as other:
            other.add(competitor)
            await other.commit()
        return found

    monkeypatch.setattr(users_crud, "_by_auth_user_id", miss_then_lose_the_race)
    async with SessionLocal() as db:
        user = await users_crud.get_or_create_from_token(db, claims)

    assert user.id == competitor.id
