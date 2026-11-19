"""argon2id password hashing (argon2-cffi defaults: RFC 9106 low-memory profile).

argon2 is deliberately slow and memory-hard, so request handlers use the ``*_async`` helpers:
they run the work in a worker thread (keeping the event loop free) behind a small semaphore
(``MAX_CONCURRENT_HASHES``) so a burst of logins can't exhaust memory or every thread.
"""

from __future__ import annotations

import asyncio
import weakref

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 1024
MAX_CONCURRENT_HASHES = 4

_hasher = PasswordHasher()
# Verified against when the account doesn't exist or has no password, so a miss costs the same
# time as a hit (no user enumeration through timing).
_DUMMY_HASH = _hasher.hash("holdfast-timing-equalizer-not-a-password")

# One semaphore per event loop (tests run several loops; a semaphore is bound to the first).
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = _semaphores[loop] = asyncio.Semaphore(MAX_CONCURRENT_HASHES)
    return sem


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """True if ``password`` matches. Always does one full argon2 verification."""
    try:
        matched = _hasher.verify(password_hash or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return matched and password_hash is not None


async def hash_password_async(password: str) -> str:
    async with _semaphore():
        return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    async with _semaphore():
        return await asyncio.to_thread(verify_password, password_hash, password)


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
