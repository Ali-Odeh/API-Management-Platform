"""MT.2 stories: workspaces, roles and isolation, through the real stack.

The tests' own workspace `e2e-tenant` is reused by every run: workspaces can
only be deleted from MT.5 on. Everything created inside it is deleted.
"""

import httpx

from conftest import (
    CONTROL_PLANE_URL,
    MOCK_BACKEND_URL,
    TENANT,
    Session,
    World,
    wait_for,
)


def test_the_owner_sees_their_workspace_and_role(owner: httpx.Client) -> None:
    mine = owner.get("/me/workspaces").json()["items"]
    assert {"slug": TENANT, "role": "owner"}.items() <= next(
        w for w in mine if w["slug"] == TENANT
    ).items()
    assert owner.get(f"/workspaces/{TENANT}").json()["slug"] == TENANT


def test_a_member_of_one_workspace_cannot_see_another(owner: httpx.Client) -> None:
    # The person owns `e2e-tenant` but isn't a member of `default`: it doesn't
    # even exist for them.
    r = owner.get("/workspaces/default/services")
    assert r.status_code == 404
    assert r.json() == {"detail": "Workspace not found"}


def test_platform_admin_needs_mfa_outside_their_own_workspaces(
    owner: httpx.Client,
    admin: httpx.Client,
    platform_admin_password_session: Session,
) -> None:
    url = f"{CONTROL_PLANE_URL}/api/v1/workspaces/{TENANT}/services"
    r = httpx.get(url, headers=platform_admin_password_session.headers, timeout=10)
    assert r.status_code == 403
    assert r.json() == {"detail": "mfa_required"}
    assert admin.get(f"/workspaces/{TENANT}/services").status_code == 200  # with MFA


def test_the_bare_host_serves_only_default(
    owner: httpx.Client, world: World, gateway: httpx.Client
) -> None:
    # Path prefixes are unique per workspace. A request without a workspace
    # subdomain goes to `default` (MT.7), so another workspace's route on the
    # same path must never answer there (test_tenancy.py covers subdomains).
    prefix = world.prefix("shared")
    tenant = f"/workspaces/{TENANT}"
    service = owner.post(
        f"{tenant}/services",
        json={"name": world.name("tenant-backend"), "upstream_url": MOCK_BACKEND_URL},
    )
    assert service.status_code == 201, service.text
    route = owner.post(
        f"{tenant}/routes",
        json={
            "service_id": service.json()["id"],
            "path_prefix": prefix,
            "methods": ["GET"],
            "auth_required": False,
        },
    )
    try:
        assert route.status_code == 201, route.text
        # The same prefix in `default`, with another upstream path to tell them apart.
        ours = world.service("default-backend", upstream_url=f"{MOCK_BACKEND_URL}/from-default")
        world.route(ours, "shared", auth_required=False)

        r = wait_for(lambda: gateway.get(f"{prefix}/x"), 200)

        assert r.status_code == 200, r.text
        assert r.json()["path"] == f"/from-default{prefix}/x"
    finally:
        if route.status_code == 201:
            owner.delete(f"{tenant}/routes/{route.json()['id']}")
        owner.delete(f"{tenant}/services/{service.json()['id']}")
