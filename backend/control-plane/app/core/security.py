"""Password hashing (argon2), access tokens (JWT) and API keys (SHA-256)."""

import hashlib
import secrets
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
    subject = payload["sub"]
    return subject if isinstance(subject, str) else None


# ---- API keys ----

API_KEY_PREFIX = "ak_live_"
_API_KEY_RANDOM_BYTES = 16  # 128 bits of randomness -> 32 hex characters


def generate_api_key() -> str:
    """A new raw key: "ak_live_" + 32 random hex characters (plan section 5).

    `secrets` uses the operating system's secure random source. Python's
    `random` module is predictable and must never be used for secrets.
    """
    return API_KEY_PREFIX + secrets.token_hex(_API_KEY_RANDOM_BYTES)


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex digest: what we store and what the gateway looks up.

    A fast hash is right here (unlike passwords): the key is 128 random bits,
    so guessing is hopeless anyway, and the same key must always give the
    same hash so the gateway can find it without a salt.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()
