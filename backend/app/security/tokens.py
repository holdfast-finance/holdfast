"""Opaque random tokens (sessions, invites, setup) and how they are stored (sha256 only)."""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32


def new_token() -> str:
    """32 random bytes, urlsafe base64 (43 chars). Never log the return value."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> bytes:
    """sha256 of the token: what the database or Redis keeps instead of the token."""
    return hashlib.sha256(token.encode("utf-8")).digest()


def token_hash_hex(token: str) -> str:
    return token_hash(token).hex()


def same_hash(a: bytes | str, b: bytes | str) -> bool:
    """Constant-time comparison of two hashes."""
    left = a.encode() if isinstance(a, str) else a
    right = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(left, right)
