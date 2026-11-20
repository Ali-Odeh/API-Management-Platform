"""MT.1 stories: logging in with GoTrue, MFA for the Platform Admin, disabling a person.

Every call goes to the real GoTrue and the real control plane; nothing is signed
by the tests themselves.
"""

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest

from conftest import CONTROL_PLANE_URL, GoTrue, Session

# A test person the tests manage through GoTrue's admin API. A fixed address,
# so repeated runs reuse one account instead of piling up new ones.
PERSON_EMAIL = "e2e-person@example.test"


def _me(session: Session) -> httpx.Response:
    return httpx.get(f"{CONTROL_PLANE_URL}/api/v1/me", headers=session.headers, timeout=10)


def test_platform_admin_logs_in_with_password_then_totp(
    platform_admin_password_session: Session, platform_admin_session: Session
) -> None:
    assert platform_admin_password_session.claims["aal"] == "aal1"
    assert platform_admin_session.claims["aal"] == "aal2"

    r = _me(platform_admin_session)

    assert r.status_code == 200
    assert r.json()["email"] == os.environ["PLATFORM_ADMIN_EMAIL"].lower()
    assert r.json()["is_platform_admin"] is True


def test_admin_api_works_with_gotrue_tokens(admin: httpx.Client) -> None:
    assert admin.get("/services").status_code == 200


def test_platform_endpoint_without_mfa_is_refused(
    platform_admin_password_session: Session,
) -> None:
    r = httpx.patch(
        f"{CONTROL_PLANE_URL}/api/v1/users/{uuid.uuid4()}",
        json={"is_active": False},
        headers=platform_admin_password_session.headers,
        timeout=10,
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "mfa_required"}


def test_a_token_gotrue_did_not_sign_is_refused() -> None:
    forged = Session("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl", "")
    assert _me(forged).status_code == 401


@pytest.fixture
def person(gotrue: GoTrue) -> Session:
    """A signed-in person with no rights, as a new team member will be."""
    password = uuid.uuid4().hex  # a new one each run; 32 characters
    gotrue.ensure_account(PERSON_EMAIL, password)
    return gotrue.sign_in(PERSON_EMAIL, password)


@pytest.fixture
def person_id(admin: httpx.Client, person: Session) -> Iterator[str]:
    """The person's id in the control plane (their first request creates the row).

    Always enabled again afterwards, so the next run starts from the same state.
    """
    r = _me(person)
    assert r.status_code == 200, r.text
    user_id: str = r.json()["id"]
    yield user_id
    assert admin.patch(f"/users/{user_id}", json={"is_active": True}).status_code == 200


def test_a_person_without_rights_cannot_use_the_admin_api(person: Session) -> None:
    r = httpx.get(f"{CONTROL_PLANE_URL}/api/v1/services", headers=person.headers, timeout=10)
    assert r.status_code == 403
    assert r.json() == {"detail": "platform_admin_required"}


def test_disabled_person_is_refused_at_once_and_cannot_refresh(
    admin: httpx.Client, gotrue: GoTrue, person: Session, person_id: str
) -> None:
    r = admin.patch(f"/users/{person_id}", json={"is_active": False})
    assert r.status_code == 200, r.text

    # The token they already hold is still unexpired, yet the next request fails...
    refused = _me(person)
    assert refused.status_code == 403
    assert refused.json() == {"detail": "user_disabled"}
    # ...and GoTrue has banned them, so they can't get a new one either.
    refresh = gotrue.refresh(person.refresh_token)
    assert refresh.status_code == 400
    assert refresh.json()["error_code"] == "user_banned"
