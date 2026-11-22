from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.merchant import MERCHANT_KEY_VERSION, merchant_key


def test_version_is_one() -> None:
    assert MERCHANT_KEY_VERSION == 1


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("Moonfern Stationery", "ignored 123", "MOONFERN STATIONERY"),
        (None, "POS DEBIT MOONFERN STATIONERY #4411", "MOONFERN STATIONERY"),
        ("  ", "CHECKCARD 0101 Sunfern Cafe", "SUNFERN CAFE"),
        ("1234 99", "Sunfern Cafe 12", "SUNFERN CAFE"),
        (None, "SQ *Sunfern Cafe", "SUNFERN CAFE"),
        (None, "TST* Sunfern/Cafe|Bar:Grill;Extra,More", "SUNFERN CAFE BAR GRILL"),
        (None, "ACH RECURRING PURCHASE Moonfern", "MOONFERN"),
        (None, "Moonfern POS Store", "MOONFERN POS STORE"),  # noise is dropped only when leading
        (None, "A1 B2 Moonfern3 Pens", "PENS"),
        (None, "Z", None),
        (None, "POS 123", None),
        (None, "", None),
        (None, "x" * 100, "X" * 64),
    ],
)
def test_merchant_key_table(name: str | None, raw: str, expected: str | None) -> None:
    assert merchant_key(name, raw) == expected


@given(st.one_of(st.none(), st.text(max_size=80)), st.text(max_size=120))
def test_key_has_no_digit_and_is_idempotent(name: str | None, raw: str) -> None:
    key = merchant_key(name, raw)
    if key is None:
        return
    assert not any(ch.isdigit() or ch.isnumeric() for ch in key)
    assert len(key) <= 64
    assert merchant_key(key, key) == key
