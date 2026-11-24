"""FE.4 in a browser: a developer signs up on the portal, confirms their
email, finds a published API, creates an app, gets a key and calls the
API from the try-it console through the gateway, then deletes the app.

The workspace's API, plan, route and service are made through the admin
API and removed afterwards, like the developer's account and emails."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from playwright.sync_api import Page, expect

from browser.conftest import Account, sign_in
from conftest import (
    MOCK_BACKEND_URL,
    TENANT,
    GoTrue,
    delete_emails,
    read_email,
    verify_link,
)


@dataclass
class PublishedApi:
    name: str
    prefix: str


@pytest.fixture
def published_api(owner: httpx.Client) -> Iterator[PublishedApi]:
    """A published API over one route of the tests' workspace, on an offered plan."""
    run = uuid.uuid4().hex[:6]
    base = f"/workspaces/{TENANT}"
    service = owner.post(
        f"{base}/services",
        json={"name": f"e2e-portal-{run}", "targets": [{"url": MOCK_BACKEND_URL}]},
    ).json()
    route = owner.post(
        f"{base}/routes",
        json={"service_id": service["id"], "path_prefix": f"/e2e-portal-{run}", "methods": ["GET"]},
    ).json()
    plan = owner.post(
        f"{base}/plans",
        json={"name": f"E2E portal {run}", "rate_limit_per_min": 60, "portal_visible": True},
    ).json()
    doc = {
        "openapi": "3.1.0",
        "info": {"title": "E2E portal", "version": "1.0"},
        "paths": {f"/e2e-portal-{run}/ping": {"get": {"summary": "Ping"}}},
    }
    api = owner.post(
        f"{base}/apis",
        json={
            "name": f"E2E portal {run}",
            "slug": f"e2e-portal-{run}",
            "openapi": doc,
            "route_ids": [route["id"]],
        },
    ).json()
    assert owner.patch(f"{base}/apis/{api['id']}", json={"status": "published"}).status_code == 200
    try:
        yield PublishedApi(name=api["name"], prefix=route["path_prefix"])
    finally:
        owner.delete(f"{base}/apis/{api['id']}")
        owner.delete(f"{base}/routes/{route['id']}")
        owner.delete(f"{base}/services/{service['id']}")
        owner.delete(f"{base}/plans/{plan['id']}")


@pytest.fixture
def developer_email(portal_gotrue: GoTrue) -> Iterator[str]:
    """A new address for the story; its account and emails are removed afterwards."""
    email = f"e2e-ui-dev-{uuid.uuid4().hex[:8]}@example.test"
    yield email
    if (account := portal_gotrue.account_id(email)) is not None:
        portal_gotrue.delete_account(account)
    delete_emails(email)


def test_developer_signs_up_gets_a_key_and_tries_the_api(
    page: Page, portal_gotrue: GoTrue, developer_email: str, published_api: PublishedApi
) -> None:
    password = uuid.uuid4().hex

    # Sign up; the confirmation link is opened from its email (Mailpit).
    page.goto("/portal/signup")
    page.get_by_label("Email").fill(developer_email)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_label("Repeat the password").fill(password)
    page.get_by_role("button", name="Sign up").click()
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible()
    portal_gotrue.accept_invitation(verify_link(read_email(developer_email)))

    # Sign in on the portal (the developers' own GoTrue).
    page.goto("/portal/login")
    page.get_by_label("Email").fill(developer_email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()

    # The catalog, the API's docs, an app on the offered plan.
    page.get_by_role("link", name=published_api.name).click()
    expect(page.get_by_text("Ping", exact=True)).to_be_visible()
    page.get_by_role("button", name="Create an app").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("App name").fill("Browser story")
    dialog.get_by_role("combobox").click()
    page.get_by_role("option").first.click()
    dialog.get_by_role("button", name="Create app").click()

    # Approved at once (the plan needs no approval): a key, shown once.
    page.get_by_role("button", name="Generate key").click()
    page.get_by_label("Name").fill("browser")
    page.get_by_role("button", name="Generate").click()
    key = page.get_by_label("New API key").inner_text()
    page.get_by_role("button", name="I copied it").click()

    # The try-it console calls the API through the gateway with that key.
    page.get_by_role("link", name=published_api.name).click()
    page.get_by_label("Path").fill(f"{published_api.prefix}/ping")
    page.get_by_label("API key").fill(key)
    page.get_by_role("button", name="Send").click()
    expect(page.get_by_label("Response body")).to_contain_text('"service": "mock-backend"')
    expect(page.get_by_text("200", exact=True)).to_be_visible()

    # The app goes, and its key with it.
    page.get_by_role("link", name="My apps").click()
    page.get_by_role("row", name="Open app Browser story").click()
    page.get_by_role("button", name="Delete app").click()
    page.get_by_role("button", name="Delete app").last.click()
    expect(page.get_by_text("No apps yet")).to_be_visible()


def test_members_and_developers_sign_in_apart(page: Page, member: Account) -> None:
    # A member's account doesn't open the portal: it is another GoTrue.
    page.goto("/portal/login")
    page.get_by_label("Email").fill(member.email)
    page.get_by_label("Password").fill(member.password)
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_have_text("Wrong email or password.")
    # ... while the dashboard lets them in.
    sign_in(page, member)
