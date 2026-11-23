"""The developer portal (P.1): developers made just in time, blocking them, and the catalog.

Which tokens open which API is in test_api_guards.py; this file is about
what a developer sees once in.
"""

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.apis.models import ApiStatus
from app.audit.models import AuditEvent
from app.core.db import SessionLocal
from app.developers.models import Developer, DeveloperStatus
from app.users.models import User
from app.workspaces.models import Workspace, WorkspaceStatus
from tests.factories import make_api, make_developer, make_route, make_service, make_workspace
from tests.fakes import bearer, make_developer_token
from tests.test_api_guards import set_status

Headers = dict[str, str]
DOC: dict[str, Any] = {"openapi": "3.1.0", "info": {"title": "Orders", "version": "1"}, "paths": {}}


def developer_headers(auth_user_id: uuid.UUID, email: str = "dev@portal.test") -> Headers:
    return bearer(make_developer_token(auth_user_id, email))


@pytest.fixture
def dev() -> Headers:
    """A developer who just signed up: no row yet."""
    return developer_headers(uuid.uuid4())


async def _events(action: str) -> list[AuditEvent]:
    async with SessionLocal() as session:
        return list(await session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


# ---- Developers, just in time


async def test_a_first_request_makes_the_developer(client: AsyncClient, dev: Headers) -> None:
    r = await client.get("/api/v1/portal/me", headers=dev)

    assert r.status_code == 200, r.text
    assert (r.json()["email"], r.json()["status"]) == ("dev@portal.test", "active")
    [event] = await _events("developer.create")
    assert event.workspace_id is None  # platform-level: no workspace yet
    assert event.actor_type == "developer"
    assert (event.actor_user_id, event.actor_email) == (
        uuid.UUID(r.json()["id"]),
        "dev@portal.test",
    )
    # The next request finds the same row and records nothing.
    again = await client.get("/api/v1/portal/me", headers=dev)
    assert again.json()["id"] == r.json()["id"]
    assert len(await _events("developer.create")) == 1


async def test_the_email_is_kept_lowercase(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/portal/me", headers=developer_headers(uuid.uuid4(), "Dev@Portal.TEST")
    )
    assert r.json()["email"] == "dev@portal.test"


async def test_another_account_with_a_known_email_is_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    # The developers' GoTrue was reset, say: the new account is someone else.
    await make_developer(db, email="dev@portal.test")
    r = await client.get("/api/v1/portal/me", headers=developer_headers(uuid.uuid4()))
    assert r.status_code == 403
    assert r.json() == {"detail": "account_conflict"}


async def test_parallel_first_requests_make_one_developer(
    client: AsyncClient, dev: Headers, db: AsyncSession
) -> None:
    answers = await asyncio.gather(
        *(client.get("/api/v1/portal/me", headers=dev) for _ in range(5))
    )
    assert {r.status_code for r in answers} == {200}
    assert len({r.json()["id"] for r in answers}) == 1
    assert await db.scalar(select(func.count()).select_from(Developer)) == 1


async def test_a_developer_is_never_a_user(client: AsyncClient, dev: Headers) -> None:
    await client.get("/api/v1/portal/me", headers=dev)
    async with SessionLocal() as session:
        emails = set(await session.scalars(select(User.email)))
    assert "dev@portal.test" not in emails


# ---- Blocking a developer (Platform Admin)


async def test_a_blocked_developer_is_refused_at_once(
    client: AsyncClient, dev: Headers, mfa_headers: Headers
) -> None:
    developer = (await client.get("/api/v1/portal/me", headers=dev)).json()

    r = await client.patch(
        f"/api/v1/developers/{developer['id']}", json={"status": "blocked"}, headers=mfa_headers
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "blocked"
    refused = await client.get("/api/v1/portal/me", headers=dev)
    assert refused.status_code == 403
    assert refused.json() == {"detail": "developer_blocked"}
    [event] = await _events("developer.block")
    assert event.changes == {"status": ["active", "blocked"]}
    assert event.actor_type == "platform_admin"

    r = await client.patch(
        f"/api/v1/developers/{developer['id']}", json={"status": "active"}, headers=mfa_headers
    )
    assert r.json()["status"] == "active"
    assert (await client.get("/api/v1/portal/me", headers=dev)).status_code == 200
    assert len(await _events("developer.unblock")) == 1


async def test_blocking_twice_records_once(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers
) -> None:
    developer = await make_developer(db)
    for _ in range(2):
        r = await client.patch(
            f"/api/v1/developers/{developer.id}", json={"status": "blocked"}, headers=mfa_headers
        )
        assert r.status_code == 200
    assert len(await _events("developer.block")) == 1
    # Nothing sent: nothing changes.
    r = await client.patch(f"/api/v1/developers/{developer.id}", json={}, headers=mfa_headers)
    assert r.json()["status"] == "blocked"


async def test_an_unknown_developer_is_404(client: AsyncClient, mfa_headers: Headers) -> None:
    r = await client.patch(
        f"/api/v1/developers/{uuid.uuid4()}", json={"status": "blocked"}, headers=mfa_headers
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "Developer not found"}


async def test_the_platform_admin_lists_developers(
    client: AsyncClient, db: AsyncSession, mfa_headers: Headers
) -> None:
    await make_developer(db, email="a@portal.test")
    await make_developer(db, email="b@portal.test", status=DeveloperStatus.BLOCKED)

    everyone = await client.get("/api/v1/developers", headers=mfa_headers)
    blocked = await client.get("/api/v1/developers?status=blocked", headers=mfa_headers)

    assert everyone.json()["total"] == 2
    assert [d["email"] for d in everyone.json()["items"]] == ["b@portal.test", "a@portal.test"]
    assert [d["email"] for d in blocked.json()["items"]] == ["b@portal.test"]


# ---- The catalog


@pytest.fixture
async def acme(db: AsyncSession) -> dict[str, uuid.UUID]:
    """Workspace `acme`: Orders (published, with a document and a route), Billing
    (deprecated), Labs (a draft)."""
    workspace = await make_workspace(db, slug="acme")
    service = await make_service(db, workspace.id)
    route = await make_route(db, workspace.id, service.id, path_prefix="/v1/orders")
    orders = await make_api(
        db,
        workspace.id,
        slug="orders",
        status=ApiStatus.PUBLISHED,
        openapi=DOC,
        description="Orders",
        route_ids=(route.id,),
    )
    billing = await make_api(db, workspace.id, slug="billing", status=ApiStatus.DEPRECATED)
    labs = await make_api(db, workspace.id, slug="labs")
    return {"workspace": workspace.id, "orders": orders.id, "billing": billing.id, "labs": labs.id}


CATALOG = "/api/v1/portal/workspaces/acme/apis"


async def test_the_catalog_shows_published_and_deprecated_apis(
    client: AsyncClient, dev: Headers, acme: dict[str, uuid.UUID]
) -> None:
    r = await client.get(CATALOG, headers=dev)

    assert r.status_code == 200, r.text
    assert r.json()["total"] == 2
    billing, orders = r.json()["items"]
    assert (billing["slug"], billing["status"]) == ("billing", "deprecated")
    assert (orders["slug"], orders["status"], orders["has_openapi"]) == (
        "orders",
        "published",
        True,
    )
    assert orders["routes"] == [
        {"path_prefix": "/v1/orders", "methods": ["GET", "POST"], "auth_required": True}
    ]
    assert "openapi" not in orders  # the list leaves documents out


async def test_one_api_comes_with_its_document(
    client: AsyncClient, dev: Headers, acme: dict[str, uuid.UUID]
) -> None:
    r = await client.get(f"{CATALOG}/{acme['orders']}", headers=dev)
    assert r.status_code == 200
    assert r.json()["openapi"] == DOC
    deprecated = await client.get(f"{CATALOG}/{acme['billing']}", headers=dev)
    assert deprecated.json()["status"] == "deprecated"


async def test_a_draft_is_not_found_like_nothing(
    client: AsyncClient, dev: Headers, acme: dict[str, uuid.UUID]
) -> None:
    draft = await client.get(f"{CATALOG}/{acme['labs']}", headers=dev)
    unknown = await client.get(f"{CATALOG}/{uuid.uuid4()}", headers=dev)
    assert draft.status_code == unknown.status_code == 404
    assert draft.json() == unknown.json() == {"detail": "API not found"}


async def test_another_workspaces_api_is_not_found_here(
    client: AsyncClient, dev: Headers, db: AsyncSession, acme: dict[str, uuid.UUID]
) -> None:
    globex = await make_workspace(db, slug="globex")
    theirs = await make_api(db, globex.id, slug="orders", status=ApiStatus.PUBLISHED)
    their_draft = await make_api(db, globex.id, slug="labs")

    # Their published API through acme's catalog, and their draft in their own.
    assert (await client.get(f"{CATALOG}/{theirs.id}", headers=dev)).status_code == 404
    r = await client.get(f"/api/v1/portal/workspaces/globex/apis/{their_draft.id}", headers=dev)
    assert r.status_code == 404
    listed = await client.get("/api/v1/portal/workspaces/globex/apis", headers=dev)
    assert [a["id"] for a in listed.json()["items"]] == [str(theirs.id)]


@pytest.mark.parametrize(
    "status", [WorkspaceStatus.SUSPENDED, WorkspaceStatus.DELETED, WorkspaceStatus.PURGED]
)
async def test_only_active_workspaces_have_a_catalog(
    client: AsyncClient, dev: Headers, acme: dict[str, uuid.UUID], status: WorkspaceStatus
) -> None:
    await set_status(acme["workspace"], status)
    for url in (CATALOG, f"{CATALOG}/{acme['orders']}"):
        r = await client.get(url, headers=dev)
        assert r.status_code == 404
        assert r.json() == {"detail": "Workspace not found"}
    listed = await client.get("/api/v1/portal/workspaces", headers=dev)
    assert listed.json() == {"items": [], "total": 0}


async def test_an_unknown_workspace_is_404(client: AsyncClient, dev: Headers) -> None:
    r = await client.get("/api/v1/portal/workspaces/nope/apis", headers=dev)
    assert r.status_code == 404


async def test_the_workspaces_with_a_catalog(
    client: AsyncClient,
    dev: Headers,
    db: AsyncSession,
    acme: dict[str, uuid.UUID],
    workspace: Workspace,
) -> None:
    # `default` has only a draft; `zeta` one published API.
    await make_api(db, workspace.id, slug="hidden")
    zeta = await make_workspace(db, slug="zeta")
    await make_api(db, zeta.id, slug="one", status=ApiStatus.PUBLISHED)

    r = await client.get("/api/v1/portal/workspaces", headers=dev)

    assert r.json() == {
        "items": [
            {"slug": "acme", "name": "Acme", "apis": 2},
            {"slug": "zeta", "name": "Zeta", "apis": 1},
        ],
        "total": 2,
    }
    page_two = await client.get("/api/v1/portal/workspaces?page=2&page_size=1", headers=dev)
    assert page_two.json() == {"items": [{"slug": "zeta", "name": "Zeta", "apis": 1}], "total": 2}


async def test_reading_the_catalog_records_nothing(
    client: AsyncClient, db: AsyncSession, acme: dict[str, uuid.UUID]
) -> None:
    developer = await make_developer(db)
    headers = developer_headers(developer.auth_user_id)
    before = await db.scalar(select(func.count()).select_from(AuditEvent))
    for url in (
        "/api/v1/portal/me",
        "/api/v1/portal/workspaces",
        CATALOG,
        f"{CATALOG}/{acme['orders']}",
    ):
        assert (await client.get(url, headers=headers)).status_code == 200
    assert await db.scalar(select(func.count()).select_from(AuditEvent)) == before


async def test_a_blocked_developer_sees_no_catalog(
    client: AsyncClient, db: AsyncSession, acme: dict[str, uuid.UUID]
) -> None:
    developer = await make_developer(db, status=DeveloperStatus.BLOCKED)
    r = await client.get(CATALOG, headers=developer_headers(developer.auth_user_id))
    assert r.status_code == 403
    assert r.json() == {"detail": "developer_blocked"}
