"""Who is calling: verifying GoTrue's access tokens (PLAN.md section 6, step 1).

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
    """The token's claims if GoTrue signed it and it is still valid, else None.

    PyJWT checks the signature, `exp` and the audience. Anything wrong or
    missing (tampered, expired, another audience, no email) gives None, and
    the caller answers 401 without saying which check failed.
    """
    try:
        payload = jwt.decode(
            token,
            get_settings().gotrue_jwt_secret,
            algorithms=[JWT_ALGORITHM],  # never let the token choose the algorithm
            audience=JWT_AUDIENCE,
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
