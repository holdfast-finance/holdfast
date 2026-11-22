"""C3c money-review fixes F1 to F5."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.domain import rule_regex
from app.domain.merchant import merchant_key
from app.domain.rules import (
    DescriptionCondition,
    Rule,
    RuleAction,
    RuleConditions,
    RuleTransaction,
    RuleValidationError,
    conditions_from_json,
    conditions_to_json,
    match,
    preview_token,
)


def fw(text: str) -> str:
    return "".join(chr(ord(c) + 0xFEE0) if c != " " else c for c in text)


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _rule(conditions: RuleConditions) -> Rule:
    return Rule(UUID(int=1), 10, True, conditions, RuleAction(UUID(int=2)), UUID(int=3), T0)


@pytest.mark.parametrize("bad", ["1.234", "0.001", "1000000000000.00", "-1000000000000"])
def test_amounts_over_two_places_or_range_are_refused(bad: str) -> None:
    with pytest.raises(RuleValidationError):
        RuleConditions(amount_min=Decimal(bad))
    with pytest.raises(RuleValidationError):
        conditions_from_json({"v": 1, "amount_min": bad})


def test_amount_bounds_accepted_and_tokens_ignore_trailing_zeros() -> None:
    RuleConditions(amount_min=Decimal("-999999999999.99"))
    a = conditions_to_json(RuleConditions(amount_equals=Decimal("12.5")))
    b = conditions_to_json(RuleConditions(amount_equals=Decimal("12.50")))
    assert a == b
    assert a["amount_equals"] == "12.50"
    r1 = _rule(RuleConditions(amount_equals=Decimal("12.5")))
    r2 = _rule(RuleConditions(amount_equals=Decimal("12.50")))
    assert preview_token(r1, ()) == preview_token(r2, ())


def test_negative_zero_is_normalised() -> None:
    obj = conditions_to_json(RuleConditions(amount_equals=Decimal("-0.00")))
    assert obj["amount_equals"] == "0.00"
    assert conditions_to_json(RuleConditions(amount_equals=Decimal("-0"))) == obj


@pytest.mark.parametrize(
    "key", ["moonfern", "MOONFERN 12", " MOONFERN", "M", "", "A" * 65, "MOON  FERN"]
)
def test_unnormalised_merchant_keys_are_refused(key: str) -> None:
    with pytest.raises(RuleValidationError):
        RuleConditions(merchant_key=key)
    with pytest.raises(RuleValidationError):
        conditions_from_json({"v": 1, "merchant_key": key, "merchant_key_version": 1})


def test_description_value_is_capped_for_literal_modes() -> None:
    DescriptionCondition("contains", "a" * 128)
    for mode in ("contains", "starts-with", "exact"):
        with pytest.raises(RuleValidationError):
            DescriptionCondition(mode, "a" * 129)  # type: ignore[arg-type]


def test_merchant_key_folds_compatibility_forms() -> None:
    assert merchant_key(None, fw("MOONFERN stationery")) == "MOONFERN STATIONERY"
    assert merchant_key("\ufb01sh market", "x") == "FISH MARKET"


def test_match_ignores_split_rows_like_preview() -> None:
    r = _rule(RuleConditions(merchant_key="MOONFERN"))
    base = RuleTransaction(
        UUID(int=9), "x", Decimal("-1.00"), UUID(int=8), T0.date(), merchant_key="MOONFERN"
    )
    assert match([r], base) is not None
    assert match([r], replace(base, is_split=True)) is None


def test_ingest_budget_is_integer_microseconds() -> None:
    assert rule_regex.INGEST_REGEX_US_PER_ROW == 500
    assert not hasattr(rule_regex, "INGEST_REGEX_MS_PER_ROW")
