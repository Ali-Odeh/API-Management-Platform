"""FE.3 in a browser: the Platform Admin, signed in with the authenticator
code, runs a workspace through its lifecycle and finds people and their
workspaces. The story's workspace ends deleted, like the API lifecycle
stories' (the purge job removes it later)."""

import uuid

from playwright.sync_api import Page, expect

from browser.conftest import Account, sign_in
from conftest import PERSON_EMAIL, TENANT


def test_platform_admin_runs_a_workspace_lifecycle(
    page: Page, platform_admin_ui: Account, member: Account
) -> None:
    slug = f"e2e-ui-{uuid.uuid4().hex[:6]}"
    sign_in(page, platform_admin_ui, "/platform")
    expect(page.get_by_role("heading", name="Workspaces")).to_be_visible()

    page.get_by_role("button", name="New workspace").click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Name").fill("E2E UI")
    dialog.get_by_label("Slug").fill(slug)
    dialog.get_by_label("First owner's email").fill(PERSON_EMAIL)
    dialog.get_by_role("button", name="Create workspace").click()
    expect(page.get_by_text(f"{PERSON_EMAIL} is its owner")).to_be_visible()

    actions = page.get_by_role("button", name=f"Actions for {slug}")
    actions.click()
    page.get_by_role("menuitem", name="Suspend").click()
    page.get_by_label("Reason (members see it)").fill("Browser story")
    page.get_by_role("button", name="Suspend").click()
    row = page.get_by_role("row").filter(has_text=slug)
    expect(row.get_by_text("Browser story")).to_be_visible()

    actions.click()
    page.get_by_role("menuitem", name="Unsuspend").click()
    expect(row.get_by_text("active")).to_be_visible()

    actions.click()
    page.get_by_role("menuitem", name="Delete").click()
    page.get_by_label("to confirm").fill(slug)
    page.get_by_role("button", name="Delete workspace").click()
    expect(row.get_by_text("Restorable until")).to_be_visible()

    # People: the new member shows up with their workspace and role.
    page.get_by_role("link", name="Users").click()
    page.get_by_label("Search by email").fill(member.email)
    page.get_by_role("button", name="Search").click()
    person = page.get_by_role("row").filter(has_text=member.email)
    expect(person.get_by_role("link", name="E2E tenant")).to_have_attribute("href", f"/w/{TENANT}")
    expect(person.get_by_text("Viewer")).to_be_visible()
