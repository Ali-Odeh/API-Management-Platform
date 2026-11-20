"""API keys: generated with `secrets`, stored as SHA-256 (plan section 5).

Logins and passwords live in GoTrue since MT.1 (see app/core/identity.py).
"""

import hashlib
import secrets

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
