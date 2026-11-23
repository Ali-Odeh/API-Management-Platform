"""Browser stories (FE): a real Chromium uses the React app on the stack.

The app is reached by its Compose name (`frontend:5173`); its dev server
forwards the API and GoTrue calls, as on a developer's machine. Accounts
and objects are set up through the APIs with the helpers of ../conftest.py
and removed afterwards, like in the API stories.
"""

import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import httpx
import pytest
from playwright.sync_api import Browser, Page, expect, sync_playwright

from conftest import (
    TENANT,
    GoTrue,
    Session,
    _wait_until_up,
    totp_code,
)

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://frontend:5173")
BROWSER_FACTOR = "e2e-browser"  # the Platform Admin's factor for these stories

expect.set_options(timeout=10_000)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    _wait_until_up(FRONTEND_URL)
    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch()
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    """A fresh browser profile per story: no session or theme left over."""
    context = browser.new_context(base_url=FRONTEND_URL, viewport={"width": 1280, "height": 900})
    page = context.new_page()
    yield page
    context.close()


class Codes:
    """TOTP codes that are never used twice: GoTrue may refuse a code it saw."""

    def __init__(self, secret: str) -> None:
        self.secret = secret
        self._last = ""

    def next(self) -> str:
        code = totp_code(self.secret)
        while code == self._last:
            time.sleep(1)
            code = totp_code(self.secret)
        self._last = code
        return code


@dataclass
class Account:
    email: str
    password: str
    codes: Codes | None = None


@pytest.fixture(scope="session")
def platform_admin_ui(
    gotrue: GoTrue, platform_admin_password_session: Session
) -> Iterator[Account]:
    """The Platform Admin with an authenticator of these stories (verified once)."""
    session = platform_admin_password_session
    user_id = session.claims["sub"]
    gotrue.remove_factors(user_id, BROWSER_FACTOR)
    factor_id, secret = gotrue.add_totp(session, BROWSER_FACTOR)
    codes = Codes(secret)
    try:
        # Verifying makes the factor count for sign-in.
        r = gotrue.http.post(f"/factors/{factor_id}/challenge", json={}, headers=session.headers)
        r = gotrue.http.post(
            f"/factors/{factor_id}/verify",
            json={"challenge_id": r.json()["id"], "code": codes.next()},
            headers=session.headers,
        )
        assert r.status_code == 200, r.text
        yield Account(
            os.environ["PLATFORM_ADMIN_EMAIL"], os.environ["PLATFORM_ADMIN_PASSWORD"], codes
        )
    finally:
        gotrue.remove_factors(user_id, BROWSER_FACTOR)


@pytest.fixture
def new_member(
    gotrue: GoTrue, admin: httpx.Client, owner: httpx.Client
) -> Iterator[Callable[[str], Account]]:
    """Makes new people with a role in the tests' workspace; all removed afterwards."""
    del owner  # makes sure the workspace exists
    made: list[tuple[str, str]] = []

    def make(role: str) -> Account:
        email = f"e2e-ui-{uuid.uuid4().hex[:8]}@example.test"
        password = uuid.uuid4().hex
        gotrue.ensure_account(email, password)
        r = admin.post(f"/workspaces/{TENANT}/members", json={"email": email, "role": role})
        assert r.status_code == 201, r.text
        made.append((email, r.json()["user_id"]))
        return Account(email, password)

    yield make
    for email, user_id in made:
        admin.delete(f"/workspaces/{TENANT}/members/{user_id}")
        if (account := gotrue.account_id(email)) is not None:
            gotrue.delete_account(account)


@pytest.fixture
def member(new_member: Callable[[str], Account]) -> Account:
    """A new person who is a Viewer of the tests' workspace."""
    return new_member("viewer")


def sign_in(page: Page, account: Account, path: str = "/") -> None:
    """Signs in on the login page like a person, with the code when asked."""
    page.goto(path)
    page.get_by_label("Email").fill(account.email)
    page.get_by_label("Password").fill(account.password)
    page.get_by_role("button", name="Sign in").click()
    if account.codes is not None:
        # The admin may hold other authenticators (the API stories enroll one).
        expect(page.get_by_label("Authentication code")).to_be_visible()
        chooser = page.get_by_label("Authenticator", exact=True)
        if chooser.count() > 0:
            chooser.click()
            page.get_by_role("option", name=BROWSER_FACTOR).click()
        page.get_by_label("Authentication code").fill(account.codes.next())
        page.get_by_role("button", name="Verify").click()
    expect(page.get_by_role("button", name="Account menu")).to_be_visible()
