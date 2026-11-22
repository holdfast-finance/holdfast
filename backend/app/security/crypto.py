"""Fernet encryption at rest for bearer credentials (ADR 0015).

``FERNET_KEY`` holds one or more comma-separated keys. The first (primary) key seals; every key
can open its own envelopes. Stored values are ASCII bytes::

    hf1.<kid>.<fernet-token>      kid = first 8 bytes of sha256(raw key), lowercase hex
    hf2.<kid>.<b64(nonce || ciphertext || tag)>    AES-256-GCM, bound to a context (below)

``hf2`` (ADR 0015 section 6) binds a value to where it is stored. The AEAD key is
``HKDF-SHA256(ikm=raw 32-byte ring key, salt=none, info=b"holdfast hf2 aead v1", length=32)``, so
the Fernet key itself is never used for a second purpose. The nonce is 12 random bytes per seal.
The associated data is ``b"hf2." + kid + b"." + ctx`` where ``ctx`` is
``household_id|table|column|<row key or setting name>`` (UTF-8). A blob moved to another
household, table, column or row fails the GCM tag, so it fails to open. ``hf2`` needs a context to
open; ``hf1`` and legacy values ignore any context (they carry no binding).

An ``hf1`` value is opened with the key its kid names and no other (no trial decryption). A value
with no key id (a bare Fernet token, the pre-ADR format) is *legacy*: it is trial-decrypted with
the ring in order. Any other ``hfN`` prefix is an unknown version and fails. Plaintext leaves this
module only as a :class:`SecretToken`. Errors never include plaintext, ciphertext or key material.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr

from app.config import get_settings
from app.security.secret_token import SecretToken

_KID = re.compile(r"[0-9a-f]{16}")
_VERSIONED = re.compile(rb"hf[0-9]")
_ENVELOPE_PREFIX = b"hf1."
_HF2_PREFIX = b"hf2."
_HF2_INFO = b"holdfast hf2 aead v1"
_NONCE_LEN = 12
_TAG_LEN = 16


class CryptoError(Exception):
    """Base class for encryption errors. Messages are fixed strings with no secret data."""


class MissingKeyError(CryptoError):
    def __init__(self) -> None:
        super().__init__("FERNET_KEY is not configured")


class InvalidKeyError(CryptoError):
    def __init__(self) -> None:
        super().__init__("FERNET_KEY is malformed")


class DuplicateKeyError(InvalidKeyError):
    def __init__(self) -> None:
        CryptoError.__init__(self, "FERNET_KEY lists the same key more than once")


class ContextError(CryptoError):
    def __init__(self) -> None:
        super().__init__("encryption context is malformed")


class DecryptionError(CryptoError):
    def __init__(self) -> None:
        super().__init__("ciphertext could not be decrypted with the configured key(s)")


def generate_key() -> str:
    """Return a new urlsafe-base64 Fernet key."""
    return Fernet.generate_key().decode("ascii")


def key_id(key: str) -> str:
    """The non-secret id of a Fernet key: 16 hex chars of sha256 over its 32 raw bytes."""
    try:
        raw = base64.urlsafe_b64decode(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise InvalidKeyError from None
    if len(raw) != 32:
        raise InvalidKeyError
    return hashlib.sha256(raw).hexdigest()[:16]


@dataclass(frozen=True)
class KeyRing:
    """Ordered Fernet keys. ``kids[0]`` is the primary (the only one that seals)."""

    kids: tuple[str, ...]
    _fernets: dict[str, Fernet] = field(repr=False)
    _aeads: dict[str, AESGCM] = field(repr=False, default_factory=dict)

    @property
    def primary_kid(self) -> str:
        return self.kids[0]

    def fernet(self, kid: str) -> Fernet | None:
        return self._fernets.get(kid)

    def aead(self, kid: str) -> AESGCM | None:
        return self._aeads.get(kid)


def make_context(household_id: uuid.UUID, table: str, column: str, row_key: str) -> str:
    """Build ``household_id|table|column|row_key``. Parts may not be empty or contain ``|``.

    ``table`` need not be a real table (``redis`` for the preview rows); ``row_key`` is a row
    key, a setting name, or any caller-chosen string that names the one place the value lives.
    """
    ctx = "|".join((str(household_id), table, column, row_key))
    _check_context(ctx)
    return ctx


def _check_context(ctx: str | None) -> bytes:
    if not isinstance(ctx, str):
        raise ContextError
    parts = ctx.split("|")
    if len(parts) != 4 or not all(parts):
        raise ContextError
    try:
        uuid.UUID(parts[0])
    except ValueError:
        raise ContextError from None
    return ctx.encode("utf-8")


def _derive_aead(raw: bytes) -> AESGCM:
    okm = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HF2_INFO).derive(raw)
    return AESGCM(okm)


def load_ring(key: SecretStr | None = None) -> KeyRing:
    """Parse ``FERNET_KEY`` (or ``key``). Rejects an empty ring, malformed keys, and duplicates."""
    secret = key if key is not None else get_settings().fernet_key
    if secret is None or not secret.get_secret_value().strip():
        raise MissingKeyError
    parts = [p.strip() for p in secret.get_secret_value().split(",") if p.strip()]
    if not parts:
        raise MissingKeyError
    fernets: dict[str, Fernet] = {}
    aeads: dict[str, AESGCM] = {}
    kids: list[str] = []
    for part in parts:
        try:
            kid = key_id(part)
            fernet = Fernet(part.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            # Re-raise without chaining so the key never shows up in a traceback.
            raise InvalidKeyError from None
        if kid in fernets:
            raise DuplicateKeyError
        fernets[kid] = fernet
        aeads[kid] = _derive_aead(base64.urlsafe_b64decode(part.encode("ascii")))
        kids.append(kid)
    return KeyRing(tuple(kids), fernets, aeads)


def seal(plaintext: SecretToken, ctx: str | None = None, *, key: SecretStr | None = None) -> bytes:
    """Encrypt under the primary key. With ``ctx``: ``hf2``, bound to it. Without: ``hf1``.

    ``hf1`` (no binding) is only for ``app_user.totp_secret_enc``. Every gated column and every
    new connector credential passes a context.
    """
    return seal_with(plaintext, load_ring(key), ctx)


def seal_with(plaintext: SecretToken, ring: KeyRing, ctx: str | None = None) -> bytes:
    if ctx is not None:
        aad = _check_context(ctx)
        aead = ring.aead(ring.primary_kid)
        if aead is None:  # unreachable: the primary kid is always in the ring
            raise InvalidKeyError
        nonce = os.urandom(_NONCE_LEN)
        kid = ring.primary_kid.encode("ascii")
        data = plaintext.reveal().encode("utf-8")
        ct = aead.encrypt(nonce, data, _HF2_PREFIX + kid + b"." + aad)
        return _HF2_PREFIX + kid + b"." + base64.urlsafe_b64encode(nonce + ct)
    fernet = ring.fernet(ring.primary_kid)
    if fernet is None:  # unreachable: the primary kid is always in the ring
        raise InvalidKeyError
    token = fernet.encrypt(plaintext.reveal().encode("utf-8"))
    return _ENVELOPE_PREFIX + ring.primary_kid.encode("ascii") + b"." + token


@dataclass(frozen=True)
class Envelope:
    """What a stored value is, judged from its bytes alone (no decryption)."""

    version: Literal["hf1", "hf2", "legacy", "unknown"]
    kid: str | None = None
    token: bytes = b""


def parse(blob: bytes) -> Envelope:
    """Classify ``blob``. ``unknown`` covers non-ASCII, empty, bad ``hf1`` and other ``hfN``."""
    if not blob or not blob.isascii():
        return Envelope("unknown")
    if blob.startswith(_ENVELOPE_PREFIX) or blob.startswith(_HF2_PREFIX):
        version = "hf1" if blob.startswith(_ENVELOPE_PREFIX) else "hf2"
        parts = blob.split(b".")
        if len(parts) != 3:
            return Envelope("unknown")
        kid = parts[1].decode("ascii")
        if not _KID.fullmatch(kid) or not parts[2]:
            return Envelope("unknown")
        return Envelope(version, kid, parts[2])  # type: ignore[arg-type]
    if _VERSIONED.match(blob):
        return Envelope("unknown")
    return Envelope("legacy", None, blob)


def is_current(blob: bytes, ring: KeyRing, *, bound: bool = False) -> bool:
    """True when ``blob`` is on the primary key in the target format.

    ``bound`` columns (the two gated ones) must be ``hf2``; the others stay ``hf1``.
    """
    env = parse(blob)
    want = "hf2" if bound else "hf1"
    return env.version == want and env.kid == ring.primary_kid


def open(blob: bytes, ctx: str | None = None, *, key: SecretStr | None = None) -> SecretToken:
    """Decrypt a stored value. Raises :class:`DecryptionError` (fixed message) on any failure.

    An ``hf2`` blob needs the ``ctx`` it was sealed with; a missing or different one fails.
    """
    return open_with(blob, load_ring(key), ctx)


def open_with(blob: bytes, ring: KeyRing, ctx: str | None = None) -> SecretToken:
    env = parse(blob)
    try:
        if env.version == "hf2":
            aead = ring.aead(env.kid or "")
            if aead is None or ctx is None:
                raise DecryptionError
            try:
                aad = _check_context(ctx)
            except ContextError:
                raise DecryptionError from None
            raw = base64.urlsafe_b64decode(env.token)
            if len(raw) < _NONCE_LEN + _TAG_LEN:
                raise DecryptionError
            kid_b = (env.kid or "").encode("ascii")
            ad = _HF2_PREFIX + kid_b + b"." + aad
            pt = aead.decrypt(raw[:_NONCE_LEN], raw[_NONCE_LEN:], ad)
            return SecretToken(pt.decode("utf-8"))
        if env.version == "hf1":
            fernet = ring.fernet(env.kid or "")
            if fernet is None:
                raise DecryptionError
            return SecretToken(fernet.decrypt(env.token).decode("utf-8"))
        if env.version == "legacy":
            for kid in ring.kids:
                fernet = ring.fernet(kid)
                if fernet is None:
                    continue
                try:
                    return SecretToken(fernet.decrypt(env.token).decode("utf-8"))
                except InvalidToken:
                    continue
    except (InvalidToken, InvalidTag, UnicodeError, ValueError):
        raise DecryptionError from None
    raise DecryptionError
