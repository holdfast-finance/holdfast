"""Money primitives: Decimal helpers, currency-checked sums, sign helpers, JSON codecs.

Rules (see .claude/rules/money.md): amounts are ``Decimal`` built from ``str``, never ``float``.
Outflow is negative, inflow positive. Full precision is kept internally; quantize to cents only
at presentation or statement-reconciliation boundaries.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal(0)

# Explicit context for money arithmetic, independent of the process-wide default (prec 28).
# 40 digits holds any NUMERIC(28,10) value through sums. Inexact is trapped so an operation that
# would silently lose digits raises instead. Rounding to cents is the one deliberate loss and
# goes through ``quantize_cents`` (no Inexact trap) or ``exact_cents`` (Inexact trapped).
MONEY_CONTEXT = Context(
    prec=40,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow, Inexact],
)
_ROUNDING_CONTEXT = Context(
    prec=40,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class CurrencyMismatchError(ValueError):
    """Raised when amounts in different currencies would be combined without conversion."""


def to_decimal(value: str | int | Decimal) -> Decimal:
    """Build a Decimal from a string, int or Decimal. Floats are rejected on purpose."""
    if isinstance(value, bool) or not isinstance(value, str | int | Decimal):
        raise TypeError(f"Money must be str, int or Decimal, not {type(value).__name__}")
    result = Decimal(value.strip() if isinstance(value, str) else value)
    if not result.is_finite():
        raise ValueError("Money must be a finite number.")
    return result


def quantize_cents(amount: Decimal) -> Decimal:
    """Round to cents using ROUND_HALF_EVEN (banker's rounding): 0.125 -> 0.12, 0.135 -> 0.14.

    Idempotent. Use only at presentation or reconciliation boundaries.
    """
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN, context=_ROUNDING_CONTEXT)


def exact_cents(amount: Decimal) -> Decimal:
    """Quantize to cents, raising ``decimal.Inexact`` if that would drop non-zero digits."""
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN, context=MONEY_CONTEXT)


def is_outflow(amount: Decimal) -> bool:
    return amount < ZERO


def is_inflow(amount: Decimal) -> bool:
    return amount > ZERO


def as_outflow(magnitude: Decimal) -> Decimal:
    """Force a spend amount negative regardless of the sign it arrived with."""
    return -abs(magnitude) if magnitude else ZERO


def as_inflow(magnitude: Decimal) -> Decimal:
    return abs(magnitude) if magnitude else ZERO


@dataclass(frozen=True, slots=True)
class Money:
    """An amount tied to a currency. Arithmetic across currencies raises."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise TypeError("Money.amount must be a finite Decimal")
        if not isinstance(self.currency, str):
            raise TypeError("Money.currency must be a str")
        code = self.currency.strip().upper()
        if len(code) != 3 or not (code.isascii() and code.isalpha()):
            raise ValueError("Money.currency must be a 3-letter ISO 4217 code")
        object.__setattr__(self, "currency", code)
        if not self.amount:
            object.__setattr__(self, "amount", ZERO)

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"Cannot combine {self.currency} with {other.currency}.")
        with localcontext(MONEY_CONTEXT):
            return Money(self.amount + other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(ZERO - self.amount, self.currency)

    def quantized(self) -> Money:
        return Money(quantize_cents(self.amount), self.currency)


def sum_money(items: Iterable[Money], currency: str) -> Money:
    """Sum amounts that must all share ``currency`` (required); any other currency raises."""
    zero = Money(ZERO, currency)  # validates and normalizes the code
    total = ZERO
    with localcontext(MONEY_CONTEXT):
        for item in items:
            if item.currency != zero.currency:
                raise CurrencyMismatchError(f"Cannot sum {item.currency} into {zero.currency}.")
            total += item.amount
    return Money(total, zero.currency)


def parse_json_decimal(text: str | bytes) -> Any:
    """``json.loads`` where floats become Decimal (exact) and ints stay ints."""
    return json.loads(text, parse_float=Decimal, parse_int=int)


class DecimalJSONEncoder(json.JSONEncoder):
    """Serializes Decimal as a string (``"-42.10"``) so APIs never emit a JSON number."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return format(o, "f")
        return super().default(o)


def dumps_json(value: Any) -> str:
    return json.dumps(value, cls=DecimalJSONEncoder)
