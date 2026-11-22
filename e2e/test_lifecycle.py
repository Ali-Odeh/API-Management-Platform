"""MT.5 stories: a workspace's life and its quotas, through the real stack.

Each run makes its own workspace (a new slug: slugs are never reused), plays
suspend, quota, delete and restore on it, and leaves it deleted at the end.
The purge job removes its data after PURGE_AFTER_DAYS.
"""

import uuid
from collections.abc import Iterator

import httpx
import pytest

from conftest import CONTROL_PLANE_URL, MOCK_BACKEND_URL, PERSON_EMAIL, Session


@pytest.fixture
def lifecycle(admin: httpx.Client, person: Session) -> Iterator[tuple[str, httpx.Client]]:
    """A fresh workspace owned by the test person: (slug, the owner's client)."""
    slug = f"e2e-lc-{uuid.uuid4().hex[:8]}"
    with httpx.Client(
        base_url=f"{CONTROL_PLANE_URL}/api/v1", headers=person.headers, timeout=10
    ) as owner:
        assert owner.get("/me").status_code == 200  # signed in once: now a user
        r = admin.post(
            "/workspaces", json={"name": "E2E lifecycle", "slug": slug, "owner_email": PERSON_EMAIL}
        )
        assert r.status_code == 201, r.text
        try:
            yield slug, owner
        finally:
            # Whatever the test left, the workspace ends deleted (never reused).
            admin.post(f"/workspaces/{slug}/unsuspend")
            admin.post(f"/workspaces/{slug}/restore")
            r = admin.request("DELETE", f"/workspaces/{slug}", json={"confirm": slug})
            assert r.status_code == 204, r.text


def _service(owner: httpx.Client, slug: str, name: str) -> httpx.Response:
    return owner.post(
        f"/workspaces/{slug}/services", json={"name": name, "targets": [{"url": MOCK_BACKEND_URL}]}
    )


def test_a_suspended_workspace_is_read_only_until_unsuspended(
    lifecycle: tuple[str, httpx.Client], admin: httpx.Client
) -> None:
    slug, owner = lifecycle
    r = admin.post(f"/workspaces/{slug}/suspend", json={"reason": "e2e abuse report"})
    assert r.status_code == 200, r.text

    detail = owner.get(f"/workspaces/{slug}")
    assert (detail.status_code, detail.json()["status_reason"]) == (200, "e2e abuse report")
    r = _service(owner, slug, "blocked")
    assert (r.status_code, r.json()) == (403, {"detail": "workspace_suspended"})
    r = owner.delete(f"/me/workspaces/{slug}")
    assert (r.status_code, r.json()) == (403, {"detail": "workspace_suspended"})

    assert admin.post(f"/workspaces/{slug}/unsuspend").status_code == 200
    r = _service(owner, slug, "allowed")
    assert r.status_code == 201, r.text
    owner.delete(f"/workspaces/{slug}/services/{r.json()['id']}")

    # Both changes are in the platform's log, not the workspace's own.
    events = admin.get("/audit-events", params={"action": "workspace.suspended"}).json()
    assert any(e["target_name"] == slug and e["workspace_id"] is None for e in events["items"])


def test_a_quota_refuses_the_one_too_many(
    lifecycle: tuple[str, httpx.Client], admin: httpx.Client
) -> None:
    slug, owner = lifecycle
    r = admin.patch(f"/workspaces/{slug}/limits", json={"max_services": 1})
    assert (r.status_code, r.json()["limits"]["max_services"]) == (200, 1)

    first = _service(owner, slug, "first")
    assert first.status_code == 201, first.text
    r = _service(owner, slug, "second")
    assert (r.status_code, r.json()) == (409, {"detail": "quota_exceeded"})
    assert owner.get(f"/workspaces/{slug}").json()["usage"]["services"] == 1
    owner.delete(f"/workspaces/{slug}/services/{first.json()['id']}")


def test_a_deleted_workspace_disappears_and_can_be_restored(
    lifecycle: tuple[str, httpx.Client], admin: httpx.Client
) -> None:
    slug, owner = lifecycle
    r = owner.request("DELETE", f"/workspaces/{slug}", json={"confirm": "not-the-slug"})
    assert r.status_code == 422

    assert owner.request("DELETE", f"/workspaces/{slug}", json={"confirm": slug}).status_code == 204
    assert owner.get(f"/workspaces/{slug}").status_code == 404
    assert slug not in [w["slug"] for w in owner.get("/me/workspaces").json()["items"]]
    r = admin.patch(f"/workspaces/{slug}/limits", json={"max_services": 1})
    assert (r.status_code, r.json()) == (404, {"detail": "Workspace not found"})
    deleted = admin.get("/workspaces", params={"status": "deleted", "page_size": 100}).json()
    assert slug in [w["slug"] for w in deleted["items"]]
    # Its slug stays taken.
    r = admin.post("/workspaces", json={"name": "x", "slug": slug, "owner_email": PERSON_EMAIL})
    assert r.status_code == 409

    assert admin.post(f"/workspaces/{slug}/restore").status_code == 200
    assert owner.get(f"/workspaces/{slug}").json()["status"] == "active"
