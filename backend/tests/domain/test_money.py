from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.money import (
    CurrencyMismatchError,
    Money,
    as_inflow,
    as_outflow,
    dumps_json,
    parse_json_decimal,
    quantize_cents,
    sum_money,
    to_decimal,
)

amounts = st.decimals(min_value=Decimal("-1e9"), max_value=Decimal("1e9"), places=4)
cents = st.decimals(min_value=Decimal("-1e9"), max_value=Decimal("1e9"), places=2)


@given(amounts)
def test_string_decimal_round_trip(d: Decimal) -> None:
    assert to_decimal(str(d)) == d


@given(amounts)
def test_quantize_idempotent(d: Decimal) -> None:
    once = quantize_cents(d)
    assert quantize_cents(once) == once


@given(st.lists(cents, max_size=20), st.integers(0, 20))
def test_sum_associative(values: list[Decimal], split: int) -> None:
    ms = [Money(v) for v in values]
    whole = sum_money(ms)
    left = sum_money(ms[:split])
    right = sum_money(ms[split:])
    assert (left + right).amount == whole.amount


@given(cents, cents)
def test_cross_currency_sum_raises(a: Decimal, b: Decimal) -> None:
    with pytest.raises(CurrencyMismatchError):
        sum_money([Money(a, "USD"), Money(b, "EUR")])
    with pytest.raises(CurrencyMismatchError):
        _ = Money(a, "USD") + Money(b, "EUR")


@given(cents)
def test_sign_helpers(d: Decimal) -> None:
    assert as_outflow(d) <= 0
    assert as_inflow(d) >= 0


@given(cents)
def test_json_amounts_are_strings(d: Decimal) -> None:
    out = dumps_json({"amount": d})
    assert f'"{format(d, "f")}"' in out
    assert parse_json_decimal(out)["amount"] == format(d, "f")


def test_half_even() -> None:
    assert quantize_cents(Decimal("0.125")) == Decimal("0.12")
    assert quantize_cents(Decimal("0.135")) == Decimal("0.14")


def test_parse_json_decimal_exact() -> None:
    parsed = parse_json_decimal('{"a": 0.1, "b": 3}')
    assert parsed["a"] == Decimal("0.1")
    assert isinstance(parsed["b"], int)


def test_float_rejected() -> None:
    with pytest.raises(TypeError):
        to_decimal(1.5)  # type: ignore[arg-type]
