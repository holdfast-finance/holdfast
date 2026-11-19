"""Fernet helpers: round-trip, wrong key, rotation, and no plaintext in errors."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.security import crypto

PLAINTEXT = "access-sandbox-00000000-fake-token"


@pytest.fixture
def key() -> SecretStr:
    return SecretStr(crypto.generate_key())


def test_round_trip(key: SecretStr) -> None:
    token = crypto.encrypt(PLAINTEXT, key=key)
    assert PLAINTEXT not in token
    assert crypto.decrypt(token, key=key) == PLAINTEXT


def test_ciphertexts_differ_per_call(key: SecretStr) -> None:
    assert crypto.encrypt(PLAINTEXT, key=key) != crypto.encrypt(PLAINTEXT, key=key)


def test_wrong_key_fails_without_leaking(key: SecretStr) -> None:
    token = crypto.encrypt(PLAINTEXT, key=key)
    other = SecretStr(crypto.generate_key())
    with pytest.raises(crypto.DecryptionError) as excinfo:
        crypto.decrypt(token, key=other)
    message = str(excinfo.value)
    assert PLAINTEXT not in message
    assert token not in message
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


def test_tampered_token_fails(key: SecretStr) -> None:
    token = crypto.encrypt(PLAINTEXT, key=key)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(tampered, key=key)


def test_key_rotation(key: SecretStr) -> None:
    old_token = crypto.encrypt(PLAINTEXT, key=key)
    new_key = crypto.generate_key()
    ring = SecretStr(f"{new_key},{key.get_secret_value()}")
    assert crypto.decrypt(old_token, key=ring) == PLAINTEXT
    rotated = crypto.rotate(old_token, key=ring)
    assert crypto.decrypt(rotated, key=SecretStr(new_key)) == PLAINTEXT


def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(crypto.MissingKeyError):
        crypto.encrypt(PLAINTEXT, key=SecretStr("  "))


def test_malformed_key_raises_without_echoing_it() -> None:
    bad = "definitely-not-a-fernet-key"
    with pytest.raises(crypto.InvalidKeyError) as excinfo:
        crypto.encrypt(PLAINTEXT, key=SecretStr(bad))
    assert bad not in str(excinfo.value)
