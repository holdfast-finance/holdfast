"""Field types shared by request schemas. Validation errors become ``validation_error`` 422."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

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


# API bounds are separate from storage precision. Fields can impose tighter limits.
# Use ASCII fixed-point strings and preserve signed zero, including "-0".
_DECIMAL_MAX_INTEGER_DIGITS = 14
_DECIMAL_MAX_SCALE = 8
_DECIMAL_PATTERN = re.compile(
    rf"^-?(?:0|[1-9][0-9]{{0,{_DECIMAL_MAX_INTEGER_DIGITS - 1}}})"
    rf"(?:\.[0-9]{{1,{_DECIMAL_MAX_SCALE}}})?$"
)


def _parse_decimal_str(value: object) -> Decimal:
    """Validate strings before parsing and bound Python Decimals before serialization."""
    if isinstance(value, str) and _DECIMAL_PATTERN.fullmatch(value):
        return Decimal(value)
    if not isinstance(value, Decimal):
        raise ValueError("Amount must be a fixed-point decimal string within bounds.")

    _, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):
        raise ValueError("Amount must be finite.")
    # A zero coefficient has only one integer digit even with a positive exponent.
    if exponent < -_DECIMAL_MAX_SCALE or (
        digits != (0,) and len(digits) + exponent > _DECIMAL_MAX_INTEGER_DIGITS
    ):
        raise ValueError("Amount exceeds the decimal string bounds.")
    return value


def _dump_decimal_str(value: Decimal) -> str:
    return format(value, "f")


class _DecimalString:
    """Marks an API decimal whose JSON schema is a string, not a number."""


DECIMAL_STRING = _DecimalString()

# One schema for every money, quantity and price field. Bare ``Decimal`` is a JSON number
# on the way in (Pydantic validation schema), which violates the money rule.
DecimalStr = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal_str),
    PlainSerializer(_dump_decimal_str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": _DECIMAL_PATTERN.pattern}),
    DECIMAL_STRING,
]


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)
