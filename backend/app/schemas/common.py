"""Field types shared by request schemas. Validation errors become ``validation_error`` 422."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

NAME_MAX = 200
# A WebAuthn response is a few KB (attestation certificate chains included); 64 KiB is ample.
CREDENTIAL_MAX_BYTES = 64 * 1024
CREDENTIAL_MAX_KEYS = 32
EMAIL_MAX = 254

FilingRole = Literal["primary", "spouse", "dependent"]
UserRole = Literal["owner", "member"]


def _name(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("A name is required.")
    if len(value) > NAME_MAX:
        raise ValueError(f"Keep names to {NAME_MAX} characters or fewer.")
    return value


def _email(value: str) -> str:
    value = value.strip().lower()
    local, sep, domain = value.partition("@")
    if not sep or not local or "." not in domain or " " in value or len(value) > EMAIL_MAX:
        raise ValueError("Enter a valid email.")
    return value


def _credential(value: dict[str, Any]) -> dict[str, Any]:
    if len(value) > CREDENTIAL_MAX_KEYS:
        raise ValueError("That passkey response isn't valid.")
    try:
        size = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        raise ValueError("That passkey response isn't valid.") from None
    if size > CREDENTIAL_MAX_BYTES:
        raise ValueError("That passkey response is too large.")
    return value


# Bounded before stripping so a huge body can't be pushed through the validators.
Name = Annotated[str, Field(max_length=NAME_MAX * 4), AfterValidator(_name)]
Email = Annotated[str, Field(max_length=EMAIL_MAX * 2), AfterValidator(_email)]
# Opaque tokens and codes: length-bounded, never echoed back.
Token = Annotated[str, Field(min_length=1, max_length=512)]
Code = Annotated[str, Field(min_length=1, max_length=64)]
# A WebAuthn credential JSON (registration or assertion), size-bounded before py_webauthn sees it.
WebAuthnCredential = Annotated[dict[str, Any], AfterValidator(_credential)]


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)
