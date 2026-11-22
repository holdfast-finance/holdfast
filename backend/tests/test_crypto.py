"""Fernet envelope: seal/open, key ids, legacy tokens, and no secret material in errors."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import uuid

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
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


HID = uuid.uuid4()


def _ctx(table: str = "household_setting", column: str = "value_enc", row: str = "k") -> str:
    return crypto.make_context(HID, table, column, row)


def test_hf2_round_trip_shape_and_fresh_nonce(key: SecretStr) -> None:
    ctx = _ctx()
    a = crypto.seal(SecretToken(PLAINTEXT), ctx, key=key)
    b = crypto.seal(SecretToken(PLAINTEXT), ctx, key=key)
    kid = crypto.key_id(key.get_secret_value())
    assert a.startswith(f"hf2.{kid}.".encode()) and a != b
    assert PLAINTEXT.encode() not in a
    assert crypto.parse(a).version == "hf2"
    assert crypto.open(a, ctx, key=key).reveal() == PLAINTEXT


@pytest.mark.parametrize(
    "other",
    [
        crypto.make_context(uuid.uuid4(), "household_setting", "value_enc", "k"),
        crypto.make_context(HID, "bank_connection", "value_enc", "k"),
        crypto.make_context(HID, "household_setting", "access_token_enc", "k"),
        crypto.make_context(HID, "household_setting", "value_enc", "other"),
    ],
)
def test_hf2_fails_for_any_other_household_table_column_or_row(other: str, key: SecretStr) -> None:
    blob = crypto.seal(SecretToken(PLAINTEXT), _ctx(), key=key)
    with pytest.raises(crypto.DecryptionError):
        crypto.open(blob, other, key=key)


def test_hf2_needs_a_context_and_rejects_a_malformed_one(key: SecretStr) -> None:
    blob = crypto.seal(SecretToken(PLAINTEXT), _ctx(), key=key)
    with pytest.raises(crypto.DecryptionError):
        crypto.open(blob, key=key)
    with pytest.raises(crypto.DecryptionError):
        crypto.open(blob, "not-a-context", key=key)
    for bad in ("", "x|a|b|c", f"{HID}|a|b", f"{HID}|a|b|c|d", f"{HID}||b|c"):
        with pytest.raises(crypto.ContextError):
            crypto.seal(SecretToken(PLAINTEXT), bad, key=key)
    with pytest.raises(crypto.ContextError):
        crypto.make_context(HID, "a|b", "c", "d")


def test_hf2_accepts_a_non_table_context(key: SecretStr) -> None:
    ctx = crypto.make_context(HID, "redis", "connection_preview", f"{uuid.uuid4()}:{uuid.uuid4()}")
    blob = crypto.seal(SecretToken(PLAINTEXT), ctx, key=key)
    assert crypto.open(blob, ctx, key=key).reveal() == PLAINTEXT


def test_hf2_tamper_kid_relabel_and_truncation_fail(key: SecretStr) -> None:
    ctx = _ctx()
    other = crypto.generate_key()
    ring = SecretStr(f"{key.get_secret_value()},{other}")
    blob = crypto.seal(SecretToken(PLAINTEXT), ctx, key=key)
    flipped = blob[:-3] + (b"AAA" if not blob.endswith(b"AAA") else b"BBB")
    relabelled = blob.replace(
        crypto.key_id(key.get_secret_value()).encode(), crypto.key_id(other).encode()
    )
    for bad in (flipped, relabelled, blob[:30], blob.rsplit(b".", 1)[0] + b"."):
        with pytest.raises(crypto.DecryptionError):
            crypto.open(bad, ctx, key=ring)


def test_hf2_opens_under_a_retired_key_and_rotation_target_is_the_primary() -> None:
    old, new = crypto.generate_key(), crypto.generate_key()
    ctx = _ctx()
    blob = crypto.seal(SecretToken(PLAINTEXT), ctx, key=SecretStr(old))
    ring = crypto.load_ring(SecretStr(f"{new},{old}"))
    assert crypto.open_with(blob, ring, ctx).reveal() == PLAINTEXT
    assert not crypto.is_current(blob, ring, bound=True)
    assert crypto.is_current(crypto.seal_with(SecretToken("x"), ring, ctx), ring, bound=True)
    assert not crypto.is_current(crypto.seal_with(SecretToken("x"), ring), ring, bound=True)


def test_hf1_and_legacy_still_open_and_ignore_a_context(key: SecretStr) -> None:
    raw = key.get_secret_value()
    ctx = _ctx()
    assert (
        crypto.open(crypto.seal(SecretToken(PLAINTEXT), key=key), ctx, key=key).reveal()
        == PLAINTEXT
    )
    assert crypto.open(_legacy(PLAINTEXT, raw), ctx, key=key).reveal() == PLAINTEXT


def test_hf2_canary_never_in_errors_or_reprs(
    key: SecretStr, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "canary-hf2-0123456789abcdef"
    token = SecretToken(canary)
    blob = crypto.seal(token, _ctx(), key=key)
    ring = crypto.load_ring(key)
    shown = [repr(token), str(token), repr(ring), repr(crypto.parse(blob))]
    with caplog.at_level(logging.DEBUG), pytest.raises(crypto.CryptoError) as excinfo:
        crypto.open(blob, _ctx(row="other"), key=key)
    shown += [str(excinfo.value), repr(excinfo.value), caplog.text]
    raw = key.get_secret_value()
    for text in shown:
        assert canary not in text and raw not in text
    assert excinfo.value.__cause__ is None


def _hkdf_sha256(ikm: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 with an empty salt, written from the spec to prove no dependence on our code."""
    prk = hmac.new(b"\x00" * 32, ikm, hashlib.sha256).digest()
    out, block = b"", b""
    i = 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def test_disaster_recovery_stock_tooling_opens_an_hf2_blob_given_the_key(key: SecretStr) -> None:
    """Documented in ADR 0015 section 6: hf2.<kid>.<b64url(nonce12 || ct || tag16)>."""
    raw_b64 = key.get_secret_value()
    ctx = _ctx("bank_connection", "access_token_enc", str(uuid.uuid4()))
    blob = crypto.seal(SecretToken(PLAINTEXT), ctx, key=key).decode("ascii")

    version, kid, body = blob.split(".")
    assert version == "hf2"
    aead_key = _hkdf_sha256(base64.urlsafe_b64decode(raw_b64), b"holdfast hf2 aead v1", 32)
    assert hashlib.sha256(base64.urlsafe_b64decode(raw_b64)).hexdigest()[:16] == kid
    data = base64.urlsafe_b64decode(body)
    aad = f"hf2.{kid}.{ctx}".encode()
    assert AESGCM(aead_key).decrypt(data[:12], data[12:], aad).decode() == PLAINTEXT
