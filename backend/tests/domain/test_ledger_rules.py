"""Pure ledger rules: amounts, splits, transfer pairs. Property tests for the invariants."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.ledger_rules import (
    InvalidAmountError,
    SplitError,
    TransferLeg,
    TransferPairError,
    validate_amount,
    validate_splits,
    validate_transfer_pair,
)

cents = st.integers(min_value=-(10**13) + 1, max_value=10**13 - 1).map(lambda n: Decimal(n) / 100)
nonzero = cents.filter(lambda d: d != 0)


@pytest.mark.parametrize("bad", [1.5, "1.00", 1, True, None])
def test_non_decimal_amounts_are_rejected(bad: object) -> None:
    with pytest.raises(InvalidAmountError):
        validate_amount(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "0.001", "1000000000000.00", "-1E+12"])
def test_unstorable_decimals_are_rejected(bad: str) -> None:
    with pytest.raises(InvalidAmountError):
        validate_amount(Decimal(bad))


@given(cents)
def test_any_cent_amount_is_accepted_unchanged(d: Decimal) -> None:
    assert validate_amount(d) == d


@given(nonzero, st.lists(st.integers(1, 10**6), min_size=1, max_size=6))
def test_exact_splits_of_any_parent_are_accepted(parent: Decimal, weights: list[int]) -> None:
    # Cut |parent| into len(weights) positive cent parts that sum exactly, then restore the sign.
    total = int(abs(parent) * 100)
    if total < len(weights):
        return
    base = [1] * len(weights)
    rest = total - len(weights)
    wsum = sum(weights)
    parts = [b + rest * w // wsum for b, w in zip(base, weights, strict=True)]
    parts[0] += total - sum(parts)
    sign = 1 if parent > 0 else -1
    amounts = [Decimal(p * sign) / 100 for p in parts]
    assert validate_splits(parent, amounts) == tuple(amounts)


@given(nonzero, st.integers(1, 5))
def test_splits_off_by_a_cent_are_rejected(parent: Decimal, n: int) -> None:
    if abs(parent) < Decimal("0.01") * (n + 1):
        return
    each = (parent / n).quantize(Decimal("0.01"))
    parts = [each] * n
    if sum(parts) == parent:
        parts[0] += Decimal("0.01") if parent > 0 else Decimal("-0.01")
    if any(p == 0 or (p < 0) != (parent < 0) for p in parts):
        return
    with pytest.raises(SplitError):
        validate_splits(parent, parts)


def test_split_sign_zero_and_empty_rules() -> None:
    with pytest.raises(SplitError):
        validate_splits(Decimal("-10.00"), [Decimal("-15.00"), Decimal("5.00")])  # sums, wrong sign
    with pytest.raises(SplitError):
        validate_splits(Decimal("-10.00"), [Decimal("-10.00"), Decimal("0.00")])
    with pytest.raises(SplitError):
        validate_splits(Decimal("-10.00"), [])
    with pytest.raises(SplitError):
        validate_splits(Decimal("0.00"), [Decimal("0.00")])
    assert validate_splits(Decimal("-0.30"), [Decimal("-0.10")] * 3)


def _leg(amount: str, account: uuid.UUID, currency: str = "USD", hh: uuid.UUID | None = None):
    return TransferLeg(uuid.uuid4(), hh or HH, account, Decimal(amount), currency)


HH = uuid.uuid4()
A1, A2 = uuid.uuid4(), uuid.uuid4()


def test_valid_pair_passes_even_when_magnitudes_differ() -> None:
    validate_transfer_pair(_leg("-100.00", A1), _leg("99.50", A2))


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (_leg("-5.00", A1), _leg("5.00", A1)),  # same account
        (_leg("-5.00", A1), _leg("-5.00", A2)),  # same sign
        (_leg("5.00", A1), _leg("5.00", A2)),
        (_leg("0.00", A1), _leg("5.00", A2)),  # zero leg
        (_leg("-5.00", A1, "USD"), _leg("5.00", A2, "EUR")),  # currency
        (_leg("-5.00", A1), _leg("5.00", A2, hh=uuid.uuid4())),  # household
    ],
)
def test_bad_pairs_are_rejected(a: TransferLeg, b: TransferLeg) -> None:
    with pytest.raises(TransferPairError):
        validate_transfer_pair(a, b)


def test_a_transaction_cannot_pair_with_itself() -> None:
    a = _leg("-5.00", A1)
    with pytest.raises(TransferPairError):
        validate_transfer_pair(a, TransferLeg(a.transaction_id, HH, A2, Decimal("5.00"), "USD"))
