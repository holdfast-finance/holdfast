"""Fernet encryption at rest for bearer credentials (Plaid/SimpleFIN tokens, exchange keys).

``FERNET_KEY`` may hold several comma-separated keys for rotation: the first encrypts, all of
them decrypt. Errors raised here never include plaintext, ciphertext or key material.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr

from app.config import get_settings


class CryptoError(Exception):
    """Base class for encryption errors. Messages are fixed strings with no secret data."""


class MissingKeyError(CryptoError):
    def __init__(self) -> None:
        super().__init__("FERNET_KEY is not configured")


class InvalidKeyError(CryptoError):
    def __init__(self) -> None:
        super().__init__("FERNET_KEY is malformed")


class DecryptionError(CryptoError):
    def __init__(self) -> None:
        super().__init__("ciphertext could not be decrypted with the configured key(s)")


def generate_key() -> str:
    """Return a new urlsafe-base64 Fernet key."""
    return Fernet.generate_key().decode("ascii")


def _fernet(key: SecretStr | None) -> MultiFernet:
    secret = key if key is not None else get_settings().fernet_key
    if secret is None or not secret.get_secret_value().strip():
        raise MissingKeyError
    parts = [p.strip() for p in secret.get_secret_value().split(",") if p.strip()]
    try:
        return MultiFernet([Fernet(p.encode("ascii")) for p in parts])
    except (ValueError, UnicodeEncodeError):
        # Re-raise without chaining so the key never shows up in a traceback.
        raise InvalidKeyError from None


def encrypt(plaintext: str, *, key: SecretStr | None = None) -> str:
    """Encrypt ``plaintext`` and return the Fernet token as text."""
    return _fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str, *, key: SecretStr | None = None) -> str:
    """Decrypt a token produced by :func:`encrypt`. Raises :class:`DecryptionError`."""
    try:
        return _fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError):
        raise DecryptionError from None


def rotate(token: str, *, key: SecretStr | None = None) -> str:
    """Re-encrypt ``token`` under the primary (first) key."""
    try:
        return _fernet(key).rotate(token.encode("ascii")).decode("ascii")
    except (InvalidToken, UnicodeError):
        raise DecryptionError from None
