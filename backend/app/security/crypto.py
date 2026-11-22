"""Fernet encryption at rest for bearer credentials (ADR 0015).

``FERNET_KEY`` holds one or more comma-separated keys. The first (primary) key seals; every key
can open its own envelopes. Stored values are ASCII bytes::

    hf1.<kid>.<fernet-token>      kid = first 8 bytes of sha256(raw key), lowercase hex

An ``hf1`` value is opened with the key its kid names and no other (no trial decryption). A value
with no key id (a bare Fernet token, the pre-ADR format) is *legacy*: it is trial-decrypted with
the ring in order. Any other ``hfN`` prefix is an unknown version and fails. Plaintext leaves this
module only as a :class:`SecretToken`. Errors never include plaintext, ciphertext or key material.
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.config import get_settings
from app.security.secret_token import SecretToken

_KID = re.compile(r"[0-9a-f]{16}")
_VERSIONED = re.compile(rb"hf[0-9]")
_ENVELOPE_PREFIX = b"hf1."


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
    _fernets: dict[str, Fernet]

    @property
    def primary_kid(self) -> str:
        return self.kids[0]

    def fernet(self, kid: str) -> Fernet | None:
        return self._fernets.get(kid)


def load_ring(key: SecretStr | None = None) -> KeyRing:
    """Parse ``FERNET_KEY`` (or ``key``). Rejects an empty ring, malformed keys, and duplicates."""
    secret = key if key is not None else get_settings().fernet_key
    if secret is None or not secret.get_secret_value().strip():
        raise MissingKeyError
    parts = [p.strip() for p in secret.get_secret_value().split(",") if p.strip()]
    if not parts:
        raise MissingKeyError
    fernets: dict[str, Fernet] = {}
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
        kids.append(kid)
    return KeyRing(tuple(kids), fernets)


def seal(plaintext: SecretToken, *, key: SecretStr | None = None) -> bytes:
    """Encrypt under the primary key and return the ``hf1.<kid>.<token>`` envelope."""
    return seal_with(plaintext, load_ring(key))


def seal_with(plaintext: SecretToken, ring: KeyRing) -> bytes:
    fernet = ring.fernet(ring.primary_kid)
    if fernet is None:  # unreachable: the primary kid is always in the ring
        raise InvalidKeyError
    token = fernet.encrypt(plaintext.reveal().encode("utf-8"))
    return _ENVELOPE_PREFIX + ring.primary_kid.encode("ascii") + b"." + token


@dataclass(frozen=True)
class Envelope:
    """What a stored value is, judged from its bytes alone (no decryption)."""

    version: Literal["hf1", "legacy", "unknown"]
    kid: str | None = None
    token: bytes = b""


def parse(blob: bytes) -> Envelope:
    """Classify ``blob``. ``unknown`` covers non-ASCII, empty, bad ``hf1`` and other ``hfN``."""
    if not blob or not blob.isascii():
        return Envelope("unknown")
    if blob.startswith(_ENVELOPE_PREFIX):
        parts = blob.split(b".")
        if len(parts) != 3:
            return Envelope("unknown")
        kid = parts[1].decode("ascii")
        if not _KID.fullmatch(kid) or not parts[2]:
            return Envelope("unknown")
        return Envelope("hf1", kid, parts[2])
    if _VERSIONED.match(blob):
        return Envelope("unknown")
    return Envelope("legacy", None, blob)


def is_current(blob: bytes, ring: KeyRing) -> bool:
    """True when ``blob`` is an ``hf1`` envelope sealed under the primary key."""
    env = parse(blob)
    return env.version == "hf1" and env.kid == ring.primary_kid


def open(blob: bytes, *, key: SecretStr | None = None) -> SecretToken:
    """Decrypt a stored value. Raises :class:`DecryptionError` (fixed message) on any failure."""
    return open_with(blob, load_ring(key))


def open_with(blob: bytes, ring: KeyRing) -> SecretToken:
    env = parse(blob)
    try:
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
    except (InvalidToken, UnicodeError):
        raise DecryptionError from None
    raise DecryptionError
