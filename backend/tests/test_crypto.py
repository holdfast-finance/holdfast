"""Fernet envelope: seal/open, key ids, legacy tokens, and no secret material in errors."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.security import crypto
from app.security.secret_token import SecretToken

PLAINTEXT = "access-sandbox-00000000-fake-token"


@pytest.fixture
def key() -> SecretStr:
    return SecretStr(crypto.generate_key())


def _legacy(plaintext: str, raw_key: str) -> bytes:
    return Fernet(raw_key.encode()).encrypt(plaintext.encode())


def test_seal_open_round_trip_and_envelope_shape(key: SecretStr) -> None:
    blob = crypto.seal(SecretToken(PLAINTEXT), key=key)
    kid = crypto.key_id(key.get_secret_value())
    assert blob.startswith(f"hf1.{kid}.".encode())
    assert PLAINTEXT.encode() not in blob
    opened = crypto.open(blob, key=key)
    assert isinstance(opened, SecretToken)
    assert opened.reveal() == PLAINTEXT


def test_kid_is_16_lowercase_hex_and_stable(key: SecretStr) -> None:
    kid = crypto.key_id(key.get_secret_value())
    assert len(kid) == 16 and kid == kid.lower()
    assert kid == crypto.key_id(key.get_secret_value())


def test_ciphertexts_differ_per_call(key: SecretStr) -> None:
    secret = SecretToken(PLAINTEXT)
    assert crypto.seal(secret, key=key) != crypto.seal(secret, key=key)


def test_wrong_key_fails_without_leaking(key: SecretStr) -> None:
    blob = crypto.seal(SecretToken(PLAINTEXT), key=key)
    with pytest.raises(crypto.DecryptionError) as excinfo:
        crypto.open(blob, key=SecretStr(crypto.generate_key()))
    message = str(excinfo.value)
    assert PLAINTEXT not in message
    assert blob.decode() not in message
    assert excinfo.value.__cause__ is None


def test_tampered_token_fails(key: SecretStr) -> None:
    blob = crypto.seal(SecretToken(PLAINTEXT), key=key)
    tampered = blob[:-4] + (b"AAAA" if not blob.endswith(b"AAAA") else b"BBBB")
    with pytest.raises(crypto.DecryptionError):
        crypto.open(tampered, key=key)


def test_hf1_opens_only_with_its_own_kid_no_trial_decrypt() -> None:
    old, new = crypto.generate_key(), crypto.generate_key()
    blob = crypto.seal(SecretToken(PLAINTEXT), key=SecretStr(old))
    ring = SecretStr(f"{new},{old}")
    assert crypto.open(blob, key=ring).reveal() == PLAINTEXT
    # Same Fernet token, relabelled with the other key's kid: must not fall back to trial.
    relabelled = blob.replace(crypto.key_id(old).encode(), crypto.key_id(new).encode())
    with pytest.raises(crypto.DecryptionError):
        crypto.open(relabelled, key=ring)
    # A kid that is not in the ring fails even though the old key is present.
    unknown = blob.replace(crypto.key_id(old).encode(), b"0" * 16)
    with pytest.raises(crypto.DecryptionError):
        crypto.open(unknown, key=ring)


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"hf1.",
        b"hf1.abc.def",
        b"hf1.ZZZZZZZZZZZZZZZZ.tok",
        b"hf1.0123456789abcdef.a.b",
        b"hf2.x.y",
    ],
)
def test_malformed_or_unknown_versions_fail_without_trial(blob: bytes, key: SecretStr) -> None:
    assert crypto.parse(blob).version == "unknown"
    with pytest.raises(crypto.DecryptionError):
        crypto.open(blob, key=key)


def test_legacy_bare_token_is_trial_decrypted_in_ring_order() -> None:
    old, new = crypto.generate_key(), crypto.generate_key()
    blob = _legacy(PLAINTEXT, old)
    assert crypto.parse(blob).version == "legacy"
    assert crypto.open(blob, key=SecretStr(f"{new},{old}")).reveal() == PLAINTEXT
    with pytest.raises(crypto.DecryptionError):
        crypto.open(blob, key=SecretStr(new))


def test_is_current_only_for_primary_kid() -> None:
    old, new = crypto.generate_key(), crypto.generate_key()
    ring = crypto.load_ring(SecretStr(f"{new},{old}"))
    assert crypto.is_current(crypto.seal(SecretToken("x"), key=SecretStr(new)), ring)
    assert not crypto.is_current(crypto.seal(SecretToken("x"), key=SecretStr(old)), ring)
    assert not crypto.is_current(_legacy("x", new), ring)


def test_malformed_key_raises_without_echoing_it() -> None:
    bad = "definitely-not-a-fernet-key"
    with pytest.raises(crypto.InvalidKeyError) as excinfo:
        crypto.seal(SecretToken(PLAINTEXT), key=SecretStr(bad))
    assert bad not in str(excinfo.value)


def test_duplicate_key_in_ring_is_rejected(key: SecretStr) -> None:
    raw = key.get_secret_value()
    with pytest.raises(crypto.DuplicateKeyError):
        crypto.load_ring(SecretStr(f"{raw},{crypto.generate_key()},{raw}"))


def test_empty_ring_is_rejected() -> None:
    with pytest.raises(crypto.MissingKeyError):
        crypto.load_ring(SecretStr(" , "))
