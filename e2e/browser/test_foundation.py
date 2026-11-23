"""FE.1 in a browser: login (with MFA), my workspaces, a workspace's frame."""

import re

from playwright.sync_api import Page, expect

from browser.conftest import Account, sign_in
from conftest import TENANT


def test_wrong_password_is_refused_in_words(page: Page, member: Account) -> None:
    page.goto("/")
    expect(page).to_have_url(re.compile(r"/login\?next="))
    page.get_by_label("Email").fill(member.email)
    page.get_by_label("Password").fill("not-the-password")
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("alert")).to_have_text("Wrong email or password.")


def test_member_signs_in_and_opens_their_workspace(page: Page, member: Account) -> None:
    sign_in(page, member)
    card = page.locator("[data-slot=card]", has_text="E2E tenant")
    expect(card.get_by_text("Viewer")).to_be_visible()
    card.get_by_role("link", name="Open").click()

    expect(page).to_have_url(re.compile(f"/w/{TENANT}$"))
    expect(page.get_by_role("heading", name="E2E tenant")).to_be_visible()
    # No Platform link for a member.
    expect(page.get_by_role("link", name="Platform")).to_have_count(0)

    # Signing out ends the session: the dashboard asks to sign in again.
    page.get_by_role("button", name="Account menu").click()
    page.get_by_role("menuitem", name="Sign out").click()
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
    page.goto(f"/w/{TENANT}")
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()


def test_platform_admin_signs_in_with_the_authenticator_code(
    page: Page, platform_admin_ui: Account
) -> None:
    sign_in(page, platform_admin_ui, "/account")
    expect(page.get_by_role("heading", name="Account and security")).to_be_visible()
    expect(page.get_by_text("verified with a code")).to_be_visible()
    expect(page.get_by_role("link", name="Platform")).to_be_visible()
