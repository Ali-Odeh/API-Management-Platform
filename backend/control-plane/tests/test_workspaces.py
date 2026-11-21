"""API tests for /api/v1/workspaces and /api/v1/me/workspaces, and the slug rules."""

import inspect
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.consumers import crud as consumers_crud
from app.core.permissions import Role
from app.keys import crud as keys_crud
from app.members import crud as members_crud
from app.members.models import WorkspaceMember
from app.routes import crud as routes_crud
from app.services import crud as services_crud
from app.users.models import User
from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_member, make_user, make_workspace
from tests.fakes import bearer, make_token

WORKSPACES = "/api/v1/workspaces"
MY_WORKSPACES = "/api/v1/me/workspaces"

Headers = dict[str, str]


def _headers(user: User, *, mfa: bool = False) -> Headers:
    assert user.auth_user_id is not None
    return bearer(make_token(user.auth_user_id, user.email, aal="aal2" if mfa else "aal1"))


@pytest.fixture
async def owner(db: AsyncSession) -> User:
    return await make_user(db, email="olivia@acme.test")


def _body(**overrides: Any) -> dict[str, Any]:
    return {"name": "Acme Inc.", "slug": "acme", "owner_email": "Olivia@Acme.test", **overrides}


# ---- Create (Platform Admin with MFA)


async def test_create_makes_the_workspace_and_its_owner(
    client: AsyncClient, db: AsyncSession, admin: User, mfa_headers: Headers, owner: User
) -> None:
    r = await client.post(WORKSPACES, json=_body(), headers=mfa_headers)

    assert r.status_code == 201
    body = r.json()
    assert (body["name"], body["slug"]) == ("Acme Inc.", "acme")
    member = await db.scalar(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == uuid.UUID(body["id"]))
    )
    assert member is not None
    assert (member.user_id, member.role, member.added_by) == (owner.id, Role.OWNER, admin.id)

    # The owner can use it at once, without MFA; the admin isn't a member.
    mine = await client.get(MY_WORKSPACES, headers=_headers(owner))
    assert [(w["slug"], w["role"]) for w in mine.json()["items"]] == [("acme", "owner")]
    read = await client.get(f"{WORKSPACES}/acme", headers=_headers(owner))
    assert read.status_code == 200 and read.json()["id"] == body["id"]


@pytest.mark.parametrize(
    "slug",
    [
        "ab",  # too short
        "a" * 51,  # too long
        "Acme",  # capitals
        "-acme",  # starts with '-'
        "acme-",  # ends with '-'
        "ac_me",  # '_' is no DNS label character
        "acme.io",
        "api",  # reserved
        "health",  # reserved
    ],
)
async def test_invalid_slug_is_422(
    client: AsyncClient, mfa_headers: Headers, owner: User, slug: str
) -> None:
    r = await client.post(WORKSPACES, json=_body(slug=slug), headers=mfa_headers)
    assert r.status_code == 422


async def test_the_database_refuses_a_bad_slug_too(db: AsyncSession) -> None:
    await expect_violation(db, "ck_workspaces_slug_dns_label", make_workspace(db, slug="Bad_Slug"))


async def test_taken_slug_is_409(
    client: AsyncClient, mfa_headers: Headers, owner: User, workspace: Workspace
) -> None:
    r = await client.post(WORKSPACES, json=_body(slug="default"), headers=mfa_headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "Slug 'default' is already taken"}


@pytest.mark.parametrize(
    ("email", "detail"),
    [
        ("nobody@acme.test", "No user with this email; they must sign in once first"),
        ("disabled@acme.test", "This user is disabled"),
    ],
)
async def test_owner_must_be_an_active_user(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers, email: str, detail: str
) -> None:
    await make_user(db, email="disabled@acme.test", is_active=False)

    r = await client.post(WORKSPACES, json=_body(owner_email=email), headers=mfa_headers)

    assert r.status_code == 422
    assert r.json()["detail"].startswith(detail)
    assert await db.scalar(select(Workspace).where(Workspace.slug == "acme")) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", "  "), ("slug", "  "), ("owner_email", "  "), ("owner_email", "not-an-email")],
)
async def test_missing_or_malformed_field_is_422(
    client: AsyncClient, mfa_headers: Headers, field: str, value: str
) -> None:
    r = await client.post(WORKSPACES, json=_body(**{field: value}), headers=mfa_headers)
    assert r.status_code == 422


# ---- List (Platform Admin) and my workspaces


async def test_platform_admin_lists_every_workspace(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers, workspace: Workspace
) -> None:
    for slug in ("acme", "globex"):
        await make_workspace(db, slug=slug)

    r = await client.get(WORKSPACES, params={"page_size": 2}, headers=mfa_headers)

    assert r.json()["total"] == 3
    assert [w["slug"] for w in r.json()["items"]] == ["globex", "acme"]  # newest first


async def test_my_workspaces_lists_only_mine_with_my_role(
    client: AsyncClient, db: AsyncSession, owner: User
) -> None:
    acme = await make_workspace(db, slug="acme")
    globex = await make_workspace(db, slug="globex")
    await make_workspace(db, slug="initech")  # not mine
    await make_member(db, acme.id, owner.id, Role.OWNER)
    await make_member(db, globex.id, owner.id, Role.VIEWER)

    r = await client.get(MY_WORKSPACES, headers=_headers(owner))

    assert r.json()["total"] == 2
    assert [(w["slug"], w["role"]) for w in r.json()["items"]] == [
        ("globex", "viewer"),
        ("acme", "owner"),
    ]


async def test_a_platform_admin_lists_only_their_memberships(
    client: AsyncClient, admin: User, auth_headers: Headers, db: AsyncSession
) -> None:
    await make_workspace(db, slug="acme")  # reachable with MFA, but not "mine"
    r = await client.get(MY_WORKSPACES, headers=auth_headers)
    assert [w["slug"] for w in r.json()["items"]] == ["default"]


# ---- Platform Admin with a low role of their own


async def test_platform_admin_beyond_their_own_role_needs_mfa(
    client: AsyncClient, db: AsyncSession
) -> None:
    # A Viewer of `acme` who is also Platform Admin: reading is their role;
    # writing is platform power, so it needs MFA.
    acme = await make_workspace(db, slug="acme")
    admin = await make_user(db, email="pa@example.com", is_platform_admin=True)
    await make_member(db, acme.id, admin.id, Role.VIEWER)
    url = f"{WORKSPACES}/acme/consumers"
    body = {"name": "app", "email": "dev@example.com"}

    assert (await client.get(url, headers=_headers(admin))).status_code == 200
    denied = await client.post(url, json=body, headers=_headers(admin))
    assert denied.status_code == 403 and denied.json() == {"detail": "mfa_required"}
    assert (await client.post(url, json=body, headers=_headers(admin, mfa=True))).status_code == 201


# ---- Tenant data access always names its workspace (decision 5)

_TENANT_CRUD = [services_crud, routes_crud, consumers_crud, keys_crud, members_crud]


@pytest.mark.parametrize(
    "function",
    [
        f
        for module in _TENANT_CRUD
        for name, f in inspect.getmembers(module, inspect.isfunction)
        if f.__module__ == module.__name__ and not name.startswith("_")
    ],
    ids=lambda f: f"{f.__module__}.{f.__name__}",
)
def test_every_tenant_crud_function_takes_workspace_id_first(function: Any) -> None:
    # Forgetting the filter is the classic way data leaks between tenants;
    # a function that can't be called without a workspace can't forget it.
    params = list(inspect.signature(function).parameters)
    assert params[:2] == ["db", "workspace_id"]
