"""Field types shared by request schemas. Validation errors become ``validation_error`` 422.

Prefer ``MoneyStr``, ``PriceStr``, or ``QuantityStr`` for fields backed by the matching
storage column. ``DecimalStr`` retains the generic fixed-point bounds.
"""

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


class _DecimalString:
    """Marks an API decimal whose JSON schema is a string, not a number."""


DECIMAL_STRING = _DecimalString()


def _decimal_string(
    integer_digits: int, scale: int
) -> tuple[BeforeValidator, PlainSerializer, WithJsonSchema, _DecimalString]:
    pattern = re.compile(
        rf"^-?(?:0|[1-9][0-9]{{0,{integer_digits - 1}}})(?:\.[0-9]{{1,{scale}}})?$"
    )

    def check_bounds(value: Decimal) -> Decimal:
        sign, digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):
            raise ValueError("Amount must be finite.")
        # A zero coefficient has only one integer digit even with a positive exponent.
        if exponent < -scale or (digits != (0,) and len(digits) + exponent > integer_digits):
            raise ValueError("Amount exceeds the decimal string bounds.")
        return Decimal("0") if sign and digits == (0,) else value

    def _parse_decimal_str(value: object) -> Decimal:
        if isinstance(value, str) and pattern.fullmatch(value):
            value = Decimal(value)
        if not isinstance(value, Decimal):
            raise ValueError("Amount must be a fixed-point decimal string within bounds.")
        return check_bounds(value)

    def _dump_decimal_str(value: Decimal) -> str:
        return format(check_bounds(value), "f")

    return (
        BeforeValidator(_parse_decimal_str),
        PlainSerializer(_dump_decimal_str, return_type=str, when_used="json"),
        WithJsonSchema({"type": "string", "pattern": pattern.pattern}),
        DECIMAL_STRING,
    )


DecimalStr = Annotated[Decimal, *_decimal_string(14, 8)]
# Match the NUMERIC columns in app.models._common.
MoneyStr = Annotated[Decimal, *_decimal_string(12, 2)]
PriceStr = Annotated[Decimal, *_decimal_string(10, 8)]
QuantityStr = Annotated[Decimal, *_decimal_string(18, 10)]


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)
