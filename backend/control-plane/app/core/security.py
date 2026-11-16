"""Password hashing (argon2) and access tokens (JWT)."""

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"

_hasher = PasswordHasher()


# ---- Passwords ----


def hash_password(password: str) -> str:
    """One-way: the result contains a random salt, so the same password
    gives a different hash every time."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# A real hash of a random string. Used when the email doesn't exist, so a
# failed login takes the same time either way and an attacker can't tell
# from the response time which emails are registered.
DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


# ---- Tokens ----


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    """Make a signed JWT. `subject` is the admin id."""
    settings = get_settings()
    minutes = settings.jwt_expire_minutes if expires_minutes is None else expires_minutes
    now = datetime.now(UTC)
    payload = {"sub": subject, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Return the subject if the token is valid, else None.

    PyJWT checks the signature and the `exp` time for us. Any problem
    (tampered, expired, garbage) raises InvalidTokenError.
    """
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[JWT_ALGORITHM],  # never let the token choose the algorithm
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        return None
    return payload["sub"]
