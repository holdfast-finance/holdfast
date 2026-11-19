"""argon2id password hashing (argon2-cffi defaults: RFC 9106 low-memory profile)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 1024

_hasher = PasswordHasher()
# Verified against when the account doesn't exist or has no password, so a miss costs the same
# time as a hit (no user enumeration through timing).
_DUMMY_HASH = _hasher.hash("holdfast-timing-equalizer-not-a-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """True if ``password`` matches. Always does one full argon2 verification."""
    try:
        matched = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return matched and password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
