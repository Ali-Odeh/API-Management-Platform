"""Unit tests for app/core/security.py: API keys (no database, no HTTP)."""

import hashlib

from app.core.security import generate_api_key, hash_api_key


def test_generated_key_format() -> None:
    key = generate_api_key()
    assert key.startswith("ak_live_")
    random_part = key.removeprefix("ak_live_")
    assert len(random_part) == 32
    assert all(c in "0123456789abcdef" for c in random_part)


def test_generated_keys_are_unique() -> None:
    assert len({generate_api_key() for _ in range(1000)}) == 1000


def test_hash_api_key_is_sha256_hex_and_stable() -> None:
    key = "ak_live_" + "0" * 32
    assert hash_api_key(key) == hashlib.sha256(key.encode()).hexdigest()
    assert len(hash_api_key(key)) == 64
    assert hash_api_key(key) == hash_api_key(key)  # no salt: the gateway can look it up
    assert hash_api_key(key) != hash_api_key(key[:-1] + "1")
