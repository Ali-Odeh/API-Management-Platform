"""Unit tests for app/core/security.py (no database, no HTTP)."""

import hashlib

import jwt

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


def test_hash_is_not_the_password_and_is_salted() -> None:
    h1 = hash_password("secret123")
    h2 = hash_password("secret123")
    assert "secret123" not in h1
    assert h1.startswith("$argon2id$")
    assert h1 != h2  # random salt: same password, different hash


def test_verify_password() -> None:
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("secret123", "not-a-hash") is False


def test_token_round_trip() -> None:
    token = create_access_token("admin-42")
    assert decode_access_token(token) == "admin-42"


def test_expired_token_is_rejected() -> None:
    token = create_access_token("admin-42", expires_minutes=-1)
    assert decode_access_token(token) is None


def test_tampered_token_is_rejected() -> None:
    token = create_access_token("admin-42")
    tampered = token[:-2] + ("AA" if token[-2:] != "AA" else "BB")
    assert decode_access_token(tampered) is None


def test_token_signed_with_other_secret_is_rejected() -> None:
    forged = jwt.encode({"sub": "admin-42", "exp": 9999999999}, "not-our-secret", "HS256")
    assert decode_access_token(forged) is None


def test_token_without_exp_is_rejected() -> None:
    no_exp = jwt.encode({"sub": "admin-42"}, get_settings().jwt_secret, "HS256")
    assert decode_access_token(no_exp) is None


def test_garbage_is_rejected() -> None:
    assert decode_access_token("not.a.jwt") is None


# ---- API keys


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
