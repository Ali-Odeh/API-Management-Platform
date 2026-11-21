"""MT.4 stories: inviting someone new, and a workspace never losing its last Owner.

The invited person gets a new address every run, so the invitation email is
really sent each time. Their membership, GoTrue account and email are deleted
afterwards; their row in the platform's users table stays (people are never
deleted, only disabled).
"""

import re
import time
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from conftest import CONTROL_PLANE_URL, MAILPIT_URL, TENANT, GoTrue

MAIL_WAIT = 10.0  # seconds; GoTrue sends at once, Mailpit stores in milliseconds


def _invitation_email(address: str) -> dict[str, Any]:
    """The invitation GoTrue sent to `address`, read through Mailpit's API."""
    deadline = time.monotonic() + MAIL_WAIT
    while time.monotonic() < deadline:
        found = httpx.get(
            f"{MAILPIT_URL}/api/v1/search", params={"query": f'to:"{address}"'}, timeout=10
        ).json()["messages"]
        if found:
            message: dict[str, Any] = httpx.get(
                f"{MAILPIT_URL}/api/v1/message/{found[0]['ID']}", timeout=10
            ).json()
            return message
        time.sleep(0.2)
    pytest.fail(f"no invitation email for {address} after {MAIL_WAIT:.0f} s")


@pytest.fixture
def invitee(owner: httpx.Client, gotrue: GoTrue) -> Iterator[dict[str, str]]:
    """A new person, added to e2e-tenant as Viewer by its Owner: so invited."""
    address = f"e2e-invitee-{uuid.uuid4().hex[:8]}@example.test"
    r = owner.post(f"/workspaces/{TENANT}/members", json={"email": address, "role": "viewer"})
    assert r.status_code == 201, r.text
    assert r.json()["invited"] is True
    added = {"email": address, "user_id": r.json()["user_id"]}
    yield added
    owner.delete(f"/workspaces/{TENANT}/members/{added['user_id']}")
    found = httpx.get(
        f"{MAILPIT_URL}/api/v1/search", params={"query": f'to:"{address}"'}, timeout=10
    ).json()["messages"]
    ids = [m["ID"] for m in found]
    if ids:
        httpx.request("DELETE", f"{MAILPIT_URL}/api/v1/messages", json={"IDs": ids}, timeout=10)
    if "auth_user_id" in added:
        gotrue.delete_account(added["auth_user_id"])


def test_an_invitation_email_leads_to_a_working_membership(
    invitee: dict[str, str], gotrue: GoTrue
) -> None:
    email = _invitation_email(invitee["email"])
    assert "invited" in email["Subject"].lower()
    [link] = [
        url.replace("&amp;", "&")
        for url in re.findall(r'https?://[^\s"<>]+', email["HTML"] or email["Text"])
        if "/verify" in url
    ]

    # Accept it like a browser, choose a password, then log in normally.
    session = gotrue.accept_invitation(link)
    invitee["auth_user_id"] = session.claims["sub"]  # for the cleanup
    gotrue.set_password(session, "e2e-invitee-password")
    login = gotrue.sign_in(invitee["email"], "e2e-invitee-password")

    with httpx.Client(
        base_url=f"{CONTROL_PLANE_URL}/api/v1", headers=login.headers, timeout=10
    ) as api:
        mine = api.get("/me/workspaces").json()["items"]
        assert [(w["slug"], w["role"]) for w in mine] == [(TENANT, "viewer")]
        assert api.get(f"/workspaces/{TENANT}/services").status_code == 200  # may read
        write = api.post(
            f"/workspaces/{TENANT}/consumers", json={"name": "nope", "email": "x@example.com"}
        )
        assert write.status_code == 403  # a Viewer can't write
        assert write.json() == {"detail": "permission_denied"}


def test_the_last_owner_cannot_leave(owner: httpx.Client) -> None:
    # The test person is the only Owner of e2e-tenant.
    r = owner.delete(f"/me/workspaces/{TENANT}")
    assert r.status_code == 409
    assert r.json() == {"detail": "last_owner"}
    members = owner.get(f"/workspaces/{TENANT}/members").json()["items"]
    assert [m["role"] for m in members].count("owner") == 1


def test_an_owner_cannot_change_their_own_role(owner: httpx.Client) -> None:
    me = owner.get("/me").json()["id"]
    r = owner.patch(f"/workspaces/{TENANT}/members/{me}", json={"role": "viewer"})
    assert r.status_code == 409
