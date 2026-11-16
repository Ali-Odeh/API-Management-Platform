"""Routes: database rules. (The API arrives in step 2.2.)"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.models import Service
from app.workspaces.models import Workspace
from tests.factories import expect_violation, make_route, make_service


@pytest.fixture
async def service(db: AsyncSession, workspace: Workspace) -> Service:
    return await make_service(db, workspace.id)


async def test_defaults_are_secure(
    db: AsyncSession, workspace: Workspace, service: Service
) -> None:
    route = await make_route(db, workspace.id, service.id, methods=["GET"])
    await db.refresh(route)
    assert route.methods == ["GET"]
    assert route.auth_required is True  # needs an API key unless turned off
    assert route.strip_prefix is False
    assert route.is_active is True


async def test_root_prefix_is_allowed(
    db: AsyncSession, workspace: Workspace, service: Service
) -> None:
    await make_route(db, workspace.id, service.id, path_prefix="/")


async def test_path_prefix_unique_across_all_workspaces(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace, service: Service
) -> None:
    # MVP: the gateway matches by path only, so two tenants can't share a prefix.
    theirs = await make_service(db, other_workspace.id)
    await make_route(db, workspace.id, service.id, path_prefix="/v1/orders")
    await expect_violation(
        db,
        "uq_routes_path_prefix",
        make_route(db, other_workspace.id, theirs.id, path_prefix="/v1/orders"),
    )


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"path_prefix": "v1/orders"}, "ck_routes_path_prefix_leading_slash"),
        ({"path_prefix": "/v1/orders/"}, "ck_routes_path_prefix_no_trailing_slash"),
        ({"methods": []}, "ck_routes_methods_not_empty"),
        ({"methods": ["GET", "FETCH"]}, "ck_routes_methods_known"),
    ],
)
async def test_check_rules(
    db: AsyncSession,
    workspace: Workspace,
    service: Service,
    fields: dict[str, object],
    constraint: str,
) -> None:
    await expect_violation(db, constraint, make_route(db, workspace.id, service.id, **fields))


async def test_route_cannot_point_to_another_workspaces_service(
    db: AsyncSession, workspace: Workspace, other_workspace: Workspace
) -> None:
    # Option C safety, enforced by the database itself.
    theirs = await make_service(db, other_workspace.id)
    await expect_violation(db, "fk_routes_service", make_route(db, workspace.id, theirs.id))
