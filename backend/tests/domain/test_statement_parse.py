"""Pure statement parsing and reconciliation (no database, no files)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.fingerprint import import_fingerprint
from app.domain.statement_parse import (
    StatementParseError,
    _parse_date,
    detect_profile,
    mask_long_digit_runs,
    parse_amount,
    parse_statement,
    profile_from_mapping,
    reconcile,
)

ROW = r"^\s*(?P<date>\d\d/\d\d/\d\d)\s+(?P<desc>.+?)\s+(?P<amount>-?\$[\d,]+\.\d\d)\s+(?P<bal>.+)$"


def _profile(*, kind: str = "asset", sign: str = "as_is", extra: dict | None = None):  # type: ignore[no-untyped-def]
    data = {
        "id": "example-bank",
        "institution": "First Example Bank",
        "kind": kind,
        "fingerprint": ["First Example Bank"],
        "period": r"Period (?P<start>\d\d/\d\d/\d\d) - (?P<end>\d\d/\d\d/\d\d)",
        "period_date_format": "%m/%d/%y",
        "account": r"Account \*+(?P<last4>\d{4})",
        "opening": r"^\s*Opening\s+(?P<v>-?\$[\d,]+\.\d\d)\s*$",
        "closing": r"^\s*Closing\s+(?P<v>-?\$[\d,]+\.\d\d)\s*$",
        "groups": [{"sign": sign, "row": ROW}],
    }
    data.update(extra or {})
    return profile_from_mapping(data)


def _text(opening: str, closing: str, rows: list[str]) -> str:
    body = "\n".join(rows)
    return (
        "First Example Bank\nPeriod 01/01/19 - 01/31/19\nAccount ******0000\n"
        f"Opening   {opening}\n{body}\nClosing   {closing}\n"
    )


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("$1,234.50", "1234.50"),
        ("-$4.26", "-4.26"),
        ("(12.00)", "-12.00"),
        ("5.98-", "-5.98"),
        ("5.98 -", "-5.98"),
        ("$ 287.2 0", "287.20"),
        ("+$1,234.56", "1234.56"),
    ],
)
def test_parse_amount_reads_printed_forms_as_decimal(printed: str, expected: str) -> None:
    value = parse_amount(printed)
    assert isinstance(value, Decimal)
    assert value == Decimal(expected)


def test_parse_amount_rejects_garbage() -> None:
    with pytest.raises(StatementParseError):
        parse_amount("12.3.4")


def test_asset_statement_keeps_printed_sign_and_reconciles() -> None:
    text = _text(
        "$100.00",
        "$57.50",
        [
            "01/05/19 Example Cafe  -$12.50  $87.50",
            "01/09/19  Example Pay  $20.00  $107.50",
            "01/20/19 Example Rent  -$50.00  $57.50",
        ],
    )
    st = parse_statement(text, _profile())
    (block,) = st.blocks
    assert [t.amount for t in block.txns] == [
        Decimal("-12.50"),
        Decimal("20.00"),
        Decimal("-50.00"),
    ]
    assert block.account_last4 == "0000"
    assert block.reconciliation.status == "reconciled"
    assert st.status == "reconciled"
    assert (st.period_start, st.period_end) == (date(2019, 1, 1), date(2019, 1, 31))


def test_card_statement_negates_amounts_and_balances_owed() -> None:
    # Printed: owed 100.00 -> purchase 30.00 -> payment -50.00 -> owed 80.00.
    text = _text(
        "$100.00",
        "$80.00",
        [
            "01/05/19 Purchase - Store  $30.00  $130.00",
            "01/09/19 Payment  -$50.00  $80.00",
        ],
    )
    st = parse_statement(text, _profile(kind="card", sign="invert"))
    (block,) = st.blocks
    purchase, payment = block.txns
    assert purchase.amount == Decimal("-30.00")  # a charge is an outflow
    assert payment.amount == Decimal("50.00")  # a payment is an inflow
    assert block.opening_balance == Decimal("-100.00")  # owed is negative
    assert block.closing_balance == Decimal("-80.00")
    assert block.reconciliation.status == "reconciled"


def test_unreconciled_statement_is_flagged_never_accepted() -> None:
    text = _text("$100.00", "$90.00", ["01/05/19 Coffee  -$5.00  $95.00"])
    st = parse_statement(text, _profile())
    rec = st.blocks[0].reconciliation
    assert rec.status == "needs_review"
    assert rec.reason == "balance_mismatch"
    assert rec.diff == Decimal("-5.00")
    assert st.status == "needs_review"


def test_one_cent_difference_is_within_tolerance_two_cents_is_not() -> None:
    assert reconcile(Decimal("10.00"), Decimal("9.01"), [Decimal("-1.00")]).status == "reconciled"
    assert reconcile(Decimal("10.00"), Decimal("9.02"), [Decimal("-1.00")]).status == "needs_review"
    assert reconcile(Decimal("10.00"), Decimal("8.98"), [Decimal("-1.00")]).status == "needs_review"


def test_missing_balance_needs_review() -> None:
    assert reconcile(None, Decimal("1.00"), []).reason == "missing_balance"
    assert reconcile(Decimal("1.00"), None, []).status == "needs_review"


def test_empty_statement_with_equal_balances_reconciles() -> None:
    st = parse_statement(_text("$5.00", "$5.00", []), _profile())
    assert st.blocks[0].reconciliation.status == "reconciled"


def test_identical_same_day_rows_get_distinct_ordinals_and_fingerprints() -> None:
    text = _text(
        "$100.00",
        "$90.00",
        ["01/05/19 Coffee  -$5.00  $95.00", "01/05/19 Coffee  -$5.00  $90.00"],
    )
    a, b = parse_statement(text, _profile()).blocks[0].txns
    assert (a.ordinal, b.ordinal) == (1, 2)
    assert a.fingerprint != b.fingerprint
    # Re-parsing the same text yields the same fingerprints (idempotent re-import).
    again = parse_statement(text, _profile()).blocks[0].txns
    assert [t.fingerprint for t in again] == [a.fingerprint, b.fingerprint]


def test_fingerprint_ignores_whitespace_and_case_but_not_amount_or_date() -> None:
    d = date(2019, 1, 5)
    base = import_fingerprint(d, Decimal("-5.00"), "Coffee  Shop", 1)
    assert len(base) == 64
    assert base == import_fingerprint(d, Decimal("-5.0"), "COFFEE SHOP", 1)
    assert base != import_fingerprint(d, Decimal("-5.01"), "Example Cafe", 1)
    assert base != import_fingerprint(date(2019, 1, 6), Decimal("-5.00"), "Example Cafe", 1)
    with pytest.raises(ValueError):
        import_fingerprint(d, Decimal("1"), "x", 0)


def test_blocks_reconcile_independently_and_names_are_hashed() -> None:
    extra = {"block_start": r'^\s*"(?P<name>[^"]+)"\s*$'}
    text = (
        "First Example Bank\nPeriod 01/01/19 - 01/31/19\nAccount ******0000\n"
        "Opening   $10.00\n01/02/19 Deposit  $5.00  $15.00\nClosing   $15.00\n"
        '"Vacation Fund"\nOpening   $1.00\nClosing   $1.00\n'
    )
    st = parse_statement(text, _profile(extra=extra))
    assert len(st.blocks) == 2
    assert st.blocks[0].key == "main"
    assert st.blocks[1].key != "main"
    assert "vacation" not in st.blocks[1].key.lower()
    assert all(b.reconciliation.status == "reconciled" for b in st.blocks)


def test_one_bad_block_makes_the_statement_needs_review() -> None:
    extra = {"block_start": r'^\s*"(?P<name>[^"]+)"\s*$'}
    text = (
        "First Example Bank\nPeriod 01/01/19 - 01/31/19\nAccount ******0000\n"
        'Opening   $10.00\nClosing   $10.00\n"Pod"\nOpening   $1.00\nClosing   $2.00\n'
    )
    st = parse_statement(text, _profile(extra=extra))
    assert [b.reconciliation.status for b in st.blocks] == ["reconciled", "needs_review"]
    assert st.status == "needs_review"


def test_year_less_dates_take_the_period_year_across_a_december_january_period() -> None:
    extra = {
        "date_format": "%m-%d",
        "groups": [
            {
                "sign": "as_is",
                "row": (
                    r"^\s*(?P<date>\d\d-\d\d)\s+(?P<desc>.+?)\s+(?P<amount>[\d,]+\.\d\d)"
                    r"\s*(?P<neg>-)?\s*$"
                ),
            }
        ],
    }
    text = (
        "First Example Bank\nPeriod 12/10/18 - 01/09/19\nAccount ******0000\n"
        "Opening   $100.00\n12-20 Debit card  5.00 -\n01-02 Refund  2.00\nClosing   $97.00\n"
    )
    dec, jan = parse_statement(text, _profile(extra=extra)).blocks[0].txns
    assert dec.posted_date == date(2018, 12, 20)
    assert jan.posted_date == date(2019, 1, 2)
    assert dec.amount == Decimal("-5.00")  # trailing "-" is a debit
    assert jan.amount == Decimal("2.00")


def test_wrapped_row_joins_description_and_amount_lines() -> None:
    extra = {
        "groups": [
            {
                "sign": "as_is",
                "row": ROW,
                "wrap_start": r"^\s*(?P<date>\d\d/\d\d/\d\d)\s+(?P<desc>[A-Za-z ]+)$",
                "wrap_end": r"^\s*(?P<desc>[A-Za-z ]+?)?\s*(?P<amount>-?\$[\d,]+\.\d\d)\s*$",
            }
        ]
    }
    text = _text("$10.00", "$7.00", ["01/05/19 Long Merchant Name", "  Branch  -$3.00"])
    (txn,) = parse_statement(text, _profile(extra=extra)).blocks[0].txns
    assert txn.amount == Decimal("-3.00")
    assert txn.description == "Long Merchant Name Branch"
    assert txn.posted_date == date(2019, 1, 5)


def test_summary_profile_is_balances_only_never_reconciled() -> None:
    extra = {
        "kind": "summary",
        "balances": r"Start\s+(?P<open>\$[\d.]+)\s+End\s+(?P<close>\$[\d.]+)",
    }
    text = (
        "First Example Bank\nPeriod 01/01/19 - 01/31/19\nAccount ******0000\n"
        "Start $1.00 End $9.00\n"
    )
    st = parse_statement(text, _profile(kind="summary", extra=extra))
    assert st.status == "balances_only"
    assert st.blocks[0].txns == ()
    assert st.blocks[0].closing_balance == Decimal("9.00")


def test_missing_period_raises_without_content() -> None:
    with pytest.raises(StatementParseError, match="period_not_found"):
        parse_statement("First Example Bank\nnothing\n", _profile())


def test_detect_profile_needs_all_fingerprints_and_refuses_ties() -> None:
    a = _profile()
    b = profile_from_mapping(
        {
            "id": "other",
            "institution": "X",
            "kind": "asset",
            "fingerprint": ["First Example Bank", "Gold"],
            "period": r"(?P<end>\d)",
            "period_date_format": "%d",
        }
    )
    assert detect_profile("First Example Bank", [a, b]) is a
    assert detect_profile("First Example Bank Gold", [a, b]) is b  # most specific wins
    assert detect_profile("nothing", [a, b]) is None
    twin = profile_from_mapping(
        {**{"id": "twin"}, **{k: v for k, v in _raw().items() if k != "id"}}
    )
    assert detect_profile("First Example Bank", [a, twin]) is None  # ambiguous


def _raw() -> dict:  # type: ignore[type-arg]
    return {
        "id": "example-bank",
        "institution": "First Example Bank",
        "kind": "asset",
        "fingerprint": ["First Example Bank"],
        "period": r"(?P<end>\d)",
        "period_date_format": "%d",
    }


def test_spaced_label_macro_tolerates_kerning_gaps() -> None:
    p = profile_from_mapping({**_raw(), "fingerprint": ["~Statement Closing~"]})
    assert detect_profile("St a t e m e n t Clos ing", [p]) is p


@pytest.mark.parametrize(
    "bad", ["1.234,50", "12.345", "1,2345.00", "-5.00-", "(5.00)-", "12", "1.5"]
)
def test_parse_amount_rejects_malformed_money(bad: str) -> None:
    with pytest.raises(StatementParseError):
        parse_amount(bad)


def test_account_regex_that_does_not_match_raises() -> None:
    text = "First Example Bank\nPeriod 01/01/19 - 01/31/19\nOpening   $1.00\nClosing   $1.00\n"
    with pytest.raises(StatementParseError, match="account_not_found"):
        parse_statement(text, _profile())


def test_row_dated_outside_the_period_needs_review_even_if_balances_match() -> None:
    text = _text("$10.00", "$7.00", ["06/05/19 Example Cafe  -$3.00  $7.00"])
    st = parse_statement(text, _profile())
    assert st.blocks[0].reconciliation.reason == "date_out_of_period"
    assert st.status == "needs_review"
    near = _text("$10.00", "$7.00", ["12/20/18 Example Cafe  -$3.00  $7.00"])
    assert parse_statement(near, _profile()).status == "reconciled"  # within one month before


def test_block_with_zero_balances_is_kept() -> None:
    extra = {"block_start": r'^\s*"(?P<name>[^"]+)"\s*$'}
    text = (
        "First Example Bank\nPeriod 01/01/19 - 01/31/19\nAccount ******0000\n"
        'Opening   $0.00\nClosing   $0.00\n"Pod"\nOpening   $0.00\nClosing   $0.00\n'
    )
    st = parse_statement(text, _profile(extra=extra))
    assert len(st.blocks) == 2
    assert st.status == "reconciled"


def test_mask_long_digit_runs_hides_references_but_not_short_numbers() -> None:
    assert mask_long_digit_runs("REF 123456789012 STORE #0042") == "REF ############ STORE #0042"


def test_very_long_lines_are_skipped() -> None:
    long = "01/05/19 " + "x" * 1500 + "  -$3.00  $7.00"
    st = parse_statement(_text("$10.00", "$7.00", [long]), _profile())
    assert st.blocks[0].txns == ()
    assert st.status == "needs_review"


def _yearless(period: str, rows: list[str], closing: str) -> list[date]:
    extra = {
        "date_format": "%m-%d",
        "groups": [
            {
                "sign": "as_is",
                "row": r"^\s*(?P<date>\d\d-\d\d)\s+(?P<desc>.+?)\s+(?P<amount>[\d,]+\.\d\d)\s*$",
            }
        ],
    }
    text = (
        f"First Example Bank\nPeriod {period}\nAccount ******0000\n"
        f"Opening   $0.00\n" + "\n".join(rows) + f"\nClosing   {closing}\n"
    )
    return [t.posted_date for t in parse_statement(text, _profile(extra=extra)).blocks[0].txns]


@pytest.mark.parametrize("period", ["01/01/25 - 12/31/25", "12/01/24 - 12/31/25"])
def test_year_less_rows_prefer_the_statement_period_over_grace(period: str) -> None:
    assert _yearless(period, ["01-01 First post 1.00", "12-31 Last post 1.00"], "$2.00") == [
        date(2025, 1, 1),
        date(2025, 12, 31),
    ]


def test_year_less_row_just_after_a_january_period_end_stays_in_the_period_year() -> None:
    # 02-03 is 3 days after 01/31/25: inside the +7 day grace, so 2025, not 2024.
    assert _yearless("01/04/25 - 01/31/25", ["02-03 Late post 1.00"], "$1.00") == [date(2025, 2, 3)]


def test_year_less_row_in_january_after_a_december_end_rolls_forward() -> None:
    assert _yearless("12/01/25 - 12/31/25", ["01-02 Early post 1.00"], "$1.00") == [
        date(2026, 1, 2)
    ]


def test_year_less_leap_day_skips_years_where_it_does_not_exist() -> None:
    assert _yearless("02/01/24 - 02/29/24", ["02-29 Leap 1.00"], "$1.00") == [date(2024, 2, 29)]
    # Period end in 2025 (no Feb 29 that year): the leap day belongs to 2024, never an error.
    assert _parse_date("02-29", "%m-%d", date(2025, 3, 5), date(2025, 2, 1)) == date(2024, 2, 29)


def test_year_less_dates_inside_any_period_round_trip() -> None:
    from datetime import timedelta

    for start in (date(2023, 12, 20), date(2024, 1, 4), date(2024, 2, 10), date(2024, 12, 15)):
        end = start + timedelta(days=30)
        for i in range(31):
            d = start + timedelta(days=i)
            assert _parse_date(d.strftime("%m-%d"), "%m-%d", end, start) == d
        # and the grace days after the end
        for i in range(1, 8):
            d = end + timedelta(days=i)
            assert _parse_date(d.strftime("%m-%d"), "%m-%d", end, start) == d
