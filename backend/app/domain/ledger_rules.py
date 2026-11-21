"""Pure rules for writing the ledger: amounts, splits and transfer pairs (hard rule 1, PLAN 8).

No I/O. ``app.services.ledger_service`` loads rows, calls these checks, then persists. The database
backs the same rules with CHECKs and a deferred trigger, so these fail early with a clear error
instead of an ``IntegrityError`` at commit.

- Amounts are ``Decimal``, finite, exactly two decimal places or fewer, and fit ``NUMERIC(14,2)``.
  Outflow is negative, inflow positive; a float (or a bool) is rejected, never coerced.
- Splits sum to the parent amount **exactly**, and each part has the parent's sign (no zero parts).
- A transfer pair is two legs: different transactions on different accounts, opposite signs, one
  currency, one household. Amounts are never summed across currencies.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.domain.money import CENT, MONEY_CONTEXT, ZERO

# NUMERIC(14,2): 12 integer digits.
MAX_ABS_AMOUNT = Decimal(10) ** 12


class LedgerRuleError(ValueError):
    """A ledger write would break an invariant. The message never carries amounts."""


class InvalidAmountError(LedgerRuleError):
    pass


class SplitError(LedgerRuleError):
    pass


class TransferPairError(LedgerRuleError):
    pass


def validate_amount(amount: Decimal) -> Decimal:
    """Return ``amount`` if it is a storable money value, else raise ``InvalidAmountError``."""
    if not isinstance(amount, Decimal):
        raise InvalidAmountError("amount must be a Decimal")
    if not amount.is_finite():
        raise InvalidAmountError("amount must be finite")
    if abs(amount) >= MAX_ABS_AMOUNT:
        raise InvalidAmountError("amount is out of range")
    if amount != amount.quantize(CENT):
        raise InvalidAmountError("amount has more than two decimal places")
    return amount


def validate_splits(parent_amount: Decimal, parts: Sequence[Decimal]) -> tuple[Decimal, ...]:
    """Check that ``parts`` split ``parent_amount`` exactly. Returns the validated parts."""
    validate_amount(parent_amount)
    if not parts:
        raise SplitError("a split needs at least one part (clear the splits instead)")
    for part in parts:
        validate_amount(part)
        if part == ZERO:
            raise SplitError("a split part cannot be zero")
        if (part < ZERO) != (parent_amount < ZERO) or parent_amount == ZERO:
            raise SplitError("every split part must have the same sign as the transaction")
    with localcontext(MONEY_CONTEXT):
        total = sum(parts, ZERO)
    if total != parent_amount:
        raise SplitError("splits must sum exactly to the transaction amount")
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class TransferLeg:
    """What the pairing rules need to know about one side of a transfer."""

    transaction_id: uuid.UUID
    household_id: uuid.UUID
    account_id: uuid.UUID
    amount: Decimal
    currency: str


def validate_transfer_pair(a: TransferLeg, b: TransferLeg) -> None:
    """A transfer group is exactly two legs; raise ``TransferPairError`` if these two are not."""
    validate_amount(a.amount)
    validate_amount(b.amount)
    if a.transaction_id == b.transaction_id:
        raise TransferPairError("a transfer pairs two different transactions")
    if a.household_id != b.household_id:
        raise TransferPairError("transfer legs must belong to one household")
    if a.account_id == b.account_id:
        raise TransferPairError("transfer legs must be on different accounts")
    if a.currency != b.currency:
        raise TransferPairError("transfer legs must share a currency")
    if not ((a.amount < ZERO < b.amount) or (b.amount < ZERO < a.amount)):
        raise TransferPairError("transfer legs must have opposite signs")
