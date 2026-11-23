"""P.1: a developer signs up on the portal and browses a workspace's catalog.

A new developer signs up on the developers' GoTrue (open sign-up), confirms
their address through the email in Mailpit, and logs in. They see a
workspace's published API with its OpenAPI document; its draft, and another
workspace's draft, are 404. The same token is refused by the admin API, and
a member's token by the portal API. Two workspaces are made for the story
and end deleted (for the purge job).
"""

import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from conftest import (
    CONTROL_PLANE_URL,
    MOCK_BACKEND_URL,
    PERSON_EMAIL,
    Developer,
    GoTrue,
    Session,
    delete_emails,
    read_email,
)

DOC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "Orders", "version": "1.0"},
    "paths": {"/orders": {"get": {"responses": {"200": {"description": "The orders"}}}}},
}


@pytest.fixture
def portal() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=f"{CONTROL_PLANE_URL}/api/v1/portal", timeout=10) as client:
        yield client


def _workspace(admin: httpx.Client, prefix: str) -> str:
    slug = f"{prefix}-{uuid.uuid4().hex[:6]}"
    r = admin.post("/workspaces", json={"name": slug, "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 201, r.text
    return slug


@pytest.fixture
def shops(admin: httpx.Client, person: Session) -> Iterator[dict[str, Any]]:
    """`acme`: Orders (published, with a document and a route) and Labs (a
    draft); `globex`: a draft of its own. Both deleted at the end."""
    acme, globex = _workspace(admin, "e2e-p1"), _workspace(admin, "e2e-p1-other")
    try:
        service = admin.post(
            f"/workspaces/{acme}/services",
            json={"name": "api", "targets": [{"url": MOCK_BACKEND_URL}]},
        ).json()
        route = admin.post(
            f"/workspaces/{acme}/routes",
            json={"service_id": service["id"], "path_prefix": "/orders", "methods": ["GET"]},
        ).json()
        orders = admin.post(
            f"/workspaces/{acme}/apis",
            json={"name": "Orders", "slug": "orders", "openapi": DOC, "route_ids": [route["id"]]},
        ).json()
        r = admin.patch(f"/workspaces/{acme}/apis/{orders['id']}", json={"status": "published"})
        assert r.status_code == 200, r.text
        labs = admin.post(f"/workspaces/{acme}/apis", json={"name": "Labs", "slug": "labs"}).json()
        theirs = admin.post(
            f"/workspaces/{globex}/apis", json={"name": "Secret", "slug": "secret"}
        ).json()
        yield {
            "acme": acme,
            "globex": globex,
            "orders": orders["id"],
            "labs": labs["id"],
            "theirs": theirs["id"],
        }
    finally:
        for slug in (acme, globex):
            r = admin.request("DELETE", f"/workspaces/{slug}", json={"confirm": slug})
            assert r.status_code == 204, r.text


def test_a_developer_signs_up_and_browses_the_catalog(
    new_developer: Callable[[], Developer],
    portal: httpx.Client,
    shops: dict[str, Any],
    admin: httpx.Client,
) -> None:
    developer = new_developer()  # sign-up, the confirmation email, then a password login
    assert "confirm" in read_email(developer.email)["Subject"].lower()
    headers = developer.headers

    me = portal.get("/me", headers=headers)
    assert me.status_code == 200, me.text
    assert (me.json()["email"], me.json()["status"]) == (developer.email, "active")

    # The catalog: the published API with its document; the draft isn't there.
    acme = f"/workspaces/{shops['acme']}/apis"
    listed = portal.get(acme, headers=headers).json()
    assert [(a["name"], a["status"]) for a in listed["items"]] == [("Orders", "published")]
    assert listed["items"][0]["routes"] == [
        {"path_prefix": "/orders", "methods": ["GET"], "auth_required": True}
    ]
    orders = portal.get(f"{acme}/{shops['orders']}", headers=headers)
    assert orders.status_code == 200
    assert orders.json()["openapi"] == DOC
    workspaces = portal.get("/workspaces", params={"page_size": 100}, headers=headers).json()
    assert {"slug": shops["acme"], "name": shops["acme"], "apis": 1} in workspaces["items"]
    assert shops["globex"] not in [w["slug"] for w in workspaces["items"]]

    # A draft is 404, in this workspace and in another one.
    for url in (f"{acme}/{shops['labs']}", f"/workspaces/{shops['globex']}/apis/{shops['theirs']}"):
        r = portal.get(url, headers=headers)
        assert r.status_code == 404
        assert r.json() == {"detail": "API not found"}

    # The developer's token opens nothing else, and a member's token not the portal.
    with httpx.Client(base_url=f"{CONTROL_PLANE_URL}/api/v1", timeout=10) as api:
        for path in ("/me", f"/workspaces/{shops['acme']}/apis", "/workspaces"):
            assert api.get(path, headers=headers).status_code == 401
    assert portal.get(acme, headers=admin.headers).status_code == 401

    # Publishing is live: once deprecated, the API is still listed, and marked.
    r = admin.patch(
        f"/workspaces/{shops['acme']}/apis/{shops['orders']}", json={"status": "deprecated"}
    )
    assert r.status_code == 200
    statuses = [a["status"] for a in portal.get(acme, headers=headers).json()["items"]]
    assert statuses == ["deprecated"]


def test_a_new_developer_cannot_log_in_before_confirming(portal_gotrue: GoTrue) -> None:
    email = f"e2e-unconfirmed-{uuid.uuid4().hex[:8]}@example.test"
    password = uuid.uuid4().hex
    portal_gotrue.sign_up(email, password)
    try:
        r = portal_gotrue.http.post(
            "/token", params={"grant_type": "password"}, json={"email": email, "password": password}
        )
        assert r.status_code == 400
        assert r.json().get("error_code") == "email_not_confirmed"
    finally:
        if (account := portal_gotrue.account_id(email)) is not None:
            portal_gotrue.delete_account(account)
        delete_emails(email)
