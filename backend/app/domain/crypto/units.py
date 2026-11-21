"""Exact conversion between chain base units and ``Decimal`` quantities (hard rule 1).

``Decimal(raw).scaleb(-18)`` would round to the context precision (28 digits) and silently drop
wei, so values are built from the digit tuple instead, which is exact at any size. No float, ever.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.crypto.errors import CryptoInputError

# ``NUMERIC(38,18)`` (ADR 0020): 20 integer digits, 18 decimals.
QUANTITY_INT_DIGITS = 20
QUANTITY_SCALE = 18


def from_base_units(raw: int, decimals: int) -> Decimal:
    """``raw`` base units (satoshi, wei, token units) as a ``Decimal``, exactly."""
    if not 0 <= decimals <= QUANTITY_SCALE:
        raise CryptoInputError("That asset has more decimals than Holdfast can store.")
    sign = 1 if raw < 0 else 0
    digits = tuple(int(c) for c in str(abs(raw)))
    return Decimal((sign, digits, -decimals))


def to_base_units(quantity: Decimal, decimals: int) -> int:
    """The inverse of :func:`from_base_units`. Refuses a fraction of a base unit."""
    sign, digits, exponent = quantity.as_tuple()
    if not isinstance(exponent, int):
        raise CryptoInputError("That quantity is not a number.")
    magnitude = int("".join(map(str, digits)) or "0")
    shift = exponent + decimals
    if shift >= 0:
        raw = magnitude * 10**shift
    else:
        raw, remainder = divmod(magnitude, 10**-shift)
        if remainder:
            raise CryptoInputError("That quantity is smaller than one base unit of the asset.")
    return int(-raw if sign else raw)


def fits_quantity(quantity: Decimal) -> bool:
    """True when ``quantity`` fits ``NUMERIC(38,18)`` exactly (so the database never rounds it).

    Works on the digit tuple, because ``abs()`` and ``quantize()`` round to the context precision.
    """
    if not quantity.is_finite():
        return False
    _, digits, exponent = quantity.as_tuple()
    if not isinstance(exponent, int):
        return False
    extra = -exponent - QUANTITY_SCALE  # decimals beyond 18 must all be zeros
    if extra > 0 and any(digits[len(digits) - extra :]):
        return False
    significant = digits[next((i for i, d in enumerate(digits) if d), len(digits)) :]
    # Position of the leading digit = the number of integer digits (<= 0 for a pure fraction).
    return not significant or len(significant) + exponent <= QUANTITY_INT_DIGITS
