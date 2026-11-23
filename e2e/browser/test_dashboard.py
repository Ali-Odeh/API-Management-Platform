"""FE.2 in a browser: an API Admin sets up an API from the dashboard alone,
calls it through the gateway with the key the page showed once, and finds
the call in the logs; a Viewer sees the same pages without any controls."""

import time
import uuid
from collections.abc import Callable, Iterator

import httpx
import pytest
from playwright.sync_api import Page, expect

from browser.conftest import Account, sign_in
from conftest import GATEWAY_URL, MOCK_BACKEND_URL, TENANT

WORKSPACE = f"/w/{TENANT}"


@pytest.fixture
def run_id(admin: httpx.Client) -> Iterator[str]:
    """A name for this run's objects; whatever the story made is deleted afterwards."""
    run = f"e2e-ui-{uuid.uuid4().hex[:6]}"
    yield run
    base = f"/workspaces/{TENANT}"
    for kind, field in (("consumers", "name"), ("routes", "path_prefix"), ("services", "name")):
        items = admin.get(f"{base}/{kind}", params={"page_size": 100}).json()["items"]
        for item in items:
            if run in item[field]:
                admin.delete(f"{base}/{kind}/{item['id']}")


def test_api_admin_sets_up_an_api_and_calls_it(
    page: Page, new_member: Callable[[str], Account], run_id: str
) -> None:
    sign_in(page, new_member("api_admin"), f"{WORKSPACE}/services")

    # A service with one target.
    page.get_by_role("button", name="New service").first.click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name").fill(run_id)
    dialog.get_by_label("Target 1 URL").fill(MOCK_BACKEND_URL)
    dialog.get_by_role("button", name="Create service").click()
    expect(page.get_by_text(f"Service {run_id} created")).to_be_visible()

    # A route to it, from the service's own page.
    page.get_by_role("row", name=f"Open service {run_id}").click()
    page.get_by_role("button", name="New route").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Path prefix").fill(f"/{run_id}")
    dialog.get_by_role("button", name="Create route").click()
    expect(page.get_by_text(f"Route /{run_id} created")).to_be_visible()

    # A consumer and its key, shown once.
    page.goto(f"{WORKSPACE}/consumers")
    page.get_by_role("button", name="New consumer").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name").fill(run_id)
    dialog.get_by_label("Contact email").fill(f"{run_id}@example.com")
    dialog.get_by_role("button", name="Create consumer").click()
    page.get_by_role("row", name=f"Open consumer {run_id}").click()
    page.get_by_role("button", name="Generate key").click()
    page.get_by_label("Name").fill("browser")
    page.get_by_role("button", name="Generate").click()
    key = page.get_by_label("New API key").inner_text()
    page.get_by_role("button", name="I copied it").click()

    # The gateway answers with that key (once the config reached it).
    with httpx.Client(base_url=GATEWAY_URL, headers={"Host": f"{TENANT}.localhost"}) as gateway:
        deadline = time.monotonic() + 5
        while (r := gateway.get(f"/{run_id}/ping", headers={"X-API-Key": key})).status_code != 200:
            assert time.monotonic() < deadline, f"the gateway still answers {r.status_code}"
            time.sleep(0.2)

    # The call shows up in the logs within seconds (the usage worker stores it).
    page.goto(f"{WORKSPACE}/logs")
    entry = page.get_by_text(f"/{run_id}/ping").first
    for _ in range(5):
        try:
            expect(entry).to_be_visible(timeout=3_000)
            break
        except AssertionError:
            page.reload()
    expect(entry).to_be_visible()


def test_viewer_sees_pages_without_controls(page: Page, member: Account) -> None:
    sign_in(page, member, f"{WORKSPACE}/routes")
    expect(page.get_by_role("heading", name="Routes")).to_be_visible()
    expect(page.get_by_role("button", name="New route")).to_have_count(0)
    page.goto(f"{WORKSPACE}/consumers")
    expect(page.get_by_role("heading", name="Consumers")).to_be_visible()
    expect(page.get_by_role("button", name="New consumer")).to_have_count(0)
    # The audit log is for API Admins and Owners.
    expect(page.get_by_role("link", name="Activity")).to_have_count(0)
