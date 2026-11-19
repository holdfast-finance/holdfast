"""TOTP (RFC 6238) and recovery codes.

- Secrets are Fernet-encrypted at rest (``app.security.crypto``) by the caller.
- Verification accepts the current step +/- 1 and reports which step matched, so the caller can
  refuse to accept the same step twice (replay protection).
- Recovery codes carry ~79 random bits and are shown once; only their sha256 is stored. That is
  far past offline brute force, so a slow hash isn't needed and lookup is one indexed comparison.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import pyotp

ISSUER = "Holdfast"
STEP_SECONDS = 30
VALID_WINDOW = 1
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_name: str) -> str:
    return pyotp.TOTP(secret, issuer=ISSUER).provisioning_uri(name=account_name, issuer_name=ISSUER)


def matching_step(secret: str, code: str, *, at: float | None = None) -> int | None:
    """The time step ``code`` is valid for (within +/- 1 step), or None."""
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    now = time.time() if at is None else at
    found: int | None = None
    for offset in range(-VALID_WINDOW, VALID_WINDOW + 1):
        moment = now + offset * STEP_SECONDS
        # Compare every candidate (no early exit) to keep timing flat.
        if hmac.compare_digest(totp.at(moment), code):
            found = int(moment // STEP_SECONDS)
    return found


def new_recovery_codes() -> list[str]:
    """Ten codes like ``k7pq-3mzt-x9ra-bc4e`` (16 chars from a 31-symbol alphabet)."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
        codes.append("-".join(raw[i : i + 4] for i in range(0, 16, 4)))
    return codes


def normalize_recovery_code(code: str) -> str:
    raw = "".join(ch for ch in code.lower() if ch.isalnum())
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


def recovery_code_hash(code: str) -> str:
    return hashlib.sha256(normalize_recovery_code(code).encode("utf-8")).hexdigest()
