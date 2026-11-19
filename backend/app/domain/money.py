"""Money primitives: Decimal helpers, currency-checked sums, sign helpers, JSON codecs.

Rules (see .claude/rules/money.md): amounts are ``Decimal`` built from ``str``, never ``float``.
Outflow is negative, inflow positive. Full precision is kept internally; quantize to cents only
at presentation or statement-reconciliation boundaries.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

CENT = Decimal("0.01")
ZERO = Decimal(0)


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
    return amount.quantize(CENT, rounding=ROUND_HALF_EVEN)


def is_outflow(amount: Decimal) -> bool:
    return amount < ZERO


def is_inflow(amount: Decimal) -> bool:
    return amount > ZERO


def as_outflow(magnitude: Decimal) -> Decimal:
    """Force a spend amount negative regardless of the sign it arrived with."""
    return -abs(magnitude)


def as_inflow(magnitude: Decimal) -> Decimal:
    return abs(magnitude)


@dataclass(frozen=True, slots=True)
class Money:
    """An amount tied to a currency. Arithmetic across currencies raises."""

    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money.amount must be a Decimal")
        if not self.currency:
            raise ValueError("Money.currency is required")

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"Cannot combine {self.currency} with {other.currency}.")
        return Money(self.amount + other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def quantized(self) -> Money:
        return Money(quantize_cents(self.amount), self.currency)


def sum_money(items: Iterable[Money], currency: str = "USD") -> Money:
    """Sum amounts that must all share ``currency``; any other currency raises."""
    total = ZERO
    for item in items:
        if item.currency != currency:
            raise CurrencyMismatchError(f"Cannot sum {item.currency} into {currency}.")
        total += item.amount
    return Money(total, currency)


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
