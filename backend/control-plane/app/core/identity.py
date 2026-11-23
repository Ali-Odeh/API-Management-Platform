"""Who is calling: verifying GoTrue's access tokens (PLAN.md section 6, step 1).

Two GoTrue instances issue tokens, and each kind opens only its own API:
- `auth`: workspace members and the Platform Admin (the admin API);
- `auth-portal` (P.1): developers, with open sign-up (the portal API).
They sign with different secrets and put different audiences on their
tokens, so a token of one never passes as the other's, even if one setting
were copied by mistake.

GoTrue issues the token at login; the control plane only checks it. It
never issues tokens itself, and it reads what a person may do from its own
tables, never from the token (decision 4: no token exchange).
"""

import uuid
from dataclasses import dataclass
from typing import Literal

import jwt

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
# GoTrue puts this audience on every token it gives a signed-in person. Its
# service-role key has none, so that key can never pass as a person.
JWT_AUDIENCE = "authenticated"
# The developers' GoTrue is configured with its own audience (GOTRUE_JWT_AUD).
PORTAL_JWT_AUDIENCE = "developer"
# Authenticator assurance level: aal1 = password only, aal2 = password + MFA.
MFA_LEVEL = "aal2"

Aal = Literal["aal1", "aal2"]


@dataclass(frozen=True)
class TokenClaims:
    """The parts of a verified token the control plane uses."""

    auth_user_id: uuid.UUID  # GoTrue's user id (`sub`)
    email: str
    aal: Aal

    @property
    def has_mfa(self) -> bool:
        return self.aal == MFA_LEVEL


def verify_access_token(token: str) -> TokenClaims | None:
    """A member's token (the admin API): its claims, or None if `auth` didn't sign it."""
    return _verify(token, secret=get_settings().gotrue_jwt_secret, audience=JWT_AUDIENCE)


def verify_developer_token(token: str) -> TokenClaims | None:
    """A developer's token (the portal API): its claims, or None if `auth-portal` didn't sign it."""
    return _verify(
        token, secret=get_settings().portal_gotrue_jwt_secret, audience=PORTAL_JWT_AUDIENCE
    )


def _verify(token: str, *, secret: str, audience: str) -> TokenClaims | None:
    """The token's claims if it is signed with `secret` for `audience` and still valid.

    PyJWT checks the signature, `exp` and the audience. Anything wrong or
    missing (tampered, expired, another audience, no email) gives None, and
    the caller answers 401 without saying which check failed.
    """
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],  # never let the token choose the algorithm
            audience=audience,
            options={"require": ["exp", "sub", "aud"]},
        )
        auth_user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None

    email = payload.get("email")
    if not isinstance(email, str) or not email:
        return None
    aal: Aal = "aal2" if payload.get("aal") == MFA_LEVEL else "aal1"
    return TokenClaims(auth_user_id=auth_user_id, email=email, aal=aal)
