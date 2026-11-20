"""Unit tests for app/core/identity.py: which GoTrue tokens are trusted (no database)."""

import uuid
from datetime import timedelta
from typing import Any

import jwt
import pytest

from app.core.config import get_settings
from app.core.identity import verify_access_token
from tests.fakes import make_token

USER_ID = uuid.uuid4()
EMAIL = "dev@test.local"


def test_valid_token_gives_the_claims() -> None:
    claims = verify_access_token(make_token(USER_ID, EMAIL))
    assert claims is not None
    assert claims.auth_user_id == USER_ID
    assert claims.email == EMAIL
    assert claims.aal == "aal1" and not claims.has_mfa


def test_mfa_token_has_aal2() -> None:
    claims = verify_access_token(make_token(USER_ID, EMAIL, aal="aal2"))
    assert claims is not None and claims.has_mfa


@pytest.mark.parametrize("aal", ["aal3", "AAL2", "", None])
def test_unknown_assurance_level_counts_as_no_mfa(aal: str | None) -> None:
    claims = verify_access_token(make_token(USER_ID, EMAIL, aal=aal))  # type: ignore[arg-type]
    assert claims is not None and not claims.has_mfa


@pytest.mark.parametrize(
    ("label", "token"),
    [
        ("expired", make_token(USER_ID, EMAIL, expires_in=timedelta(seconds=-1))),
        ("other secret", make_token(USER_ID, EMAIL, secret="x" * 40)),
        ("other audience", make_token(USER_ID, EMAIL, aud="someone-else")),
        ("no audience", make_token(USER_ID, EMAIL, aud=None)),
        ("no expiry", make_token(USER_ID, EMAIL, exp=None)),
        ("no subject", make_token(USER_ID, EMAIL, sub=None)),
        ("subject not a uuid", make_token(USER_ID, EMAIL, sub="admin-42")),
        ("no email", make_token(USER_ID, EMAIL, email=None)),
        ("empty email", make_token(USER_ID, EMAIL, email="")),
        ("email not a string", make_token(USER_ID, EMAIL, email=42)),
        ("garbage", "not.a.jwt"),
    ],
)
def test_untrusted_tokens_are_rejected(label: str, token: str) -> None:
    assert verify_access_token(token) is None, label


def test_service_role_key_is_not_a_person() -> None:
    # The key to GoTrue's admin API is signed with the same secret but has no
    # audience and no subject: it must never open the admin API as someone.
    key = jwt.encode(
        {"role": "service_role", "iss": "apim", "exp": 9999999999},
        get_settings().gotrue_jwt_secret,
        "HS256",
    )
    assert verify_access_token(key) is None


def test_unsigned_token_is_rejected() -> None:
    # alg "none": the classic trick of a token that claims it needs no signature.
    payload: dict[str, Any] = {"sub": str(USER_ID), "aud": "authenticated", "email": EMAIL}
    payload["exp"] = 9999999999
    unsigned = jwt.encode(payload, key=None, algorithm="none")  # type: ignore[arg-type]
    assert verify_access_token(unsigned) is None
