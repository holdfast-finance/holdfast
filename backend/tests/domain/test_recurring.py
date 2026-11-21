from __future__ import annotations

from calendar import monthrange
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from random import Random

import pytest

from app.domain.recurring import Cadence, PostedTransaction, detect_recurring

START = date(2023, 1, 1)
END = date(2024, 12, 31)


def _transactions(
    merchant: str, dates: list[date], amounts: tuple[str, ...] = ("-20",)
) -> list[PostedTransaction]:
    return [
        PostedTransaction(
            f"{merchant}-{index}",
            "example-checking",
            day,
            Decimal(amounts[index % len(amounts)]),
            merchant,
        )
        for index, day in enumerate(dates)
    ]


def _month(offset: int, day: int) -> date:
    year, month = divmod(offset, 12)
    year += START.year
    month += 1
    return date(year, month, min(day, monthrange(year, month)[1]))


def _dates(cadence: Cadence, variant: int) -> list[date]:
    if cadence in (Cadence.WEEKLY, Cadence.BIWEEKLY):
        period = 7 if cadence == Cadence.WEEKLY else 14
        return [
            START + timedelta(days=offset)
            for offset in range(variant + 4, (END - START).days + 1, period)
        ]
    if cadence == Cadence.SEMI_MONTHLY:
        days = (15, 31) if variant % 2 else (5, 20)
        return [_month(offset, day) for offset in range(24) for day in days]
    period = {Cadence.MONTHLY: 1, Cadence.QUARTERLY: 3, Cadence.ANNUAL: 12}[cadence]
    return [_month(offset, 28 + variant) for offset in range(variant % period, 24, period)]


def test_holdout_recovers_planted_series_among_irregular_purchases() -> None:
    rng = Random(75109)  # noqa: S311 -- reproducible synthetic fixture
    history: list[PostedTransaction] = []
    expected: dict[tuple[str, str, Cadence], tuple[str, ...]] = {}
    for cadence in Cadence:
        for variant in range(4):
            # Use separate schedules, jitter, and a mix of fixed, variable, and rising bills.
            dates = _dates(cadence, variant)
            dates = [day + timedelta(days=rng.choice((-1, 0, 0, 1))) for day in dates]
            dates = [day for day in dates if START <= day <= END]
            merchant = f"Invented bill {cadence} {variant}"
            amounts = ("-52", "-64", "-43", "-60") if variant == 2 else ("-52",)
            if cadence in (Cadence.BIWEEKLY, Cadence.SEMI_MONTHLY):
                amounts = ("1425.25",)
            rows = _transactions(merchant, dates, amounts)
            if variant == 3 and len(rows) >= 8:
                rows.pop(4)
                rows[len(rows) - 2 :] = [
                    replace(row, amount=row.amount * Decimal("1.12")) for row in rows[-2:]
                ]
            history.extend(rows)
            expected[merchant, "example-checking", cadence] = tuple(str(row.id) for row in rows)

    noise_count = 0
    for merchant, count in [
        ("Invented groceries", 140),
        ("Invented coffee", 160),
        ("Invented craft stall", 40),
        ("Invented kiosk", 30),
        *[(f"Invented market seller {index}", 3) for index in range(55)],
    ]:
        days = [START + timedelta(days=rng.randrange(731)) for _ in range(count)]
        noise_amounts = tuple(str(-rng.randrange(200, 19000)) for _ in range(count))
        history.extend(_transactions(merchant, days, noise_amounts))
        noise_count += count

    rng.shuffle(history)
    found = detect_recurring(history, as_of=END)
    recovered = 0
    for series in found:
        expected_ids = expected.get((series.merchant_key, str(series.account_id), series.cadence))
        if expected_ids == series.transaction_ids:
            recovered += 1
    false_series = len(found) - recovered

    print(
        f"Holdout: {recovered}/{len(expected)} recovered, {false_series} false, {noise_count} noise"
    )
    assert len(expected) >= 20 and noise_count >= 500
    assert recovered * 100 >= len(expected) * 95
    assert false_series <= 2, [
        (s.merchant_key, s.cadence, len(s.transaction_ids), s.last_date, s.is_variable)
        for s in found
        if (s.merchant_key, str(s.account_id), s.cadence) not in expected
    ]
    assert all(Decimal("0") <= series.confidence <= Decimal("1") for series in found)
    assert {"Invented groceries", "Invented coffee"}.isdisjoint(
        series.merchant_key for series in found
    )

    rng.shuffle(history)
    assert detect_recurring(history, as_of=END) == found


def test_month_end_jitter_and_one_missed_month_preserve_schedule() -> None:
    days = [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 4, 30),
        date(2024, 6, 1),
        date(2024, 6, 28),
        date(2024, 7, 31),
    ]
    rows = _transactions("Invented journal", days)
    (series,) = detect_recurring(rows, as_of=date(2024, 8, 1))
    assert series.cadence == Cadence.MONTHLY
    assert series.transaction_ids == tuple(row.id for row in rows)
    assert series.last_date == date(2024, 7, 31)
    assert series.next_expected_date == date(2024, 8, 31)
    assert series.typical_amount == Decimal("-20")
    assert series.amount_band == (Decimal("-23"), Decimal("-17"))
    assert not series.is_variable


def test_two_consecutive_misses_split_series_and_lapse_after_grace() -> None:
    rows = _transactions("Invented studio", [_month(month, 10) for month in (0, 1, 2, 5, 6, 7)])
    assert not detect_recurring(rows, as_of=_month(4, 10))[0].lapsed
    (old,) = detect_recurring(rows, as_of=_month(4, 11))
    assert old.lapsed
    assert old.next_expected_date == _month(3, 10)
    old, resumed = detect_recurring(rows, as_of=_month(7, 11))
    assert old.transaction_ids == tuple(row.id for row in rows[:3])
    assert resumed.transaction_ids == tuple(row.id for row in rows[3:])
    assert not resumed.lapsed


def test_sign_account_and_transfer_boundaries() -> None:
    dates = [_month(month, 8) for month in range(4)]
    outflows = _transactions("Invented cooperative", dates)
    inflows = _transactions("Invented payroll", dates, ("600",))
    inflows = [replace(row, merchant_key=outflows[0].merchant_key) for row in inflows]
    transfers = [
        replace(row, merchant_key=outflows[0].merchant_key, is_transfer=True)
        for row in _transactions("Invented sweep", dates, ("800",))
    ]
    other_accounts = [
        replace(outflows[0], id=f"unrelated-{index}", account_id=f"example-savings-{index}")
        for index in range(4)
    ]
    spend, earnings = detect_recurring(outflows + inflows + transfers + other_accounts, as_of=END)
    assert spend.typical_amount == Decimal("-20")
    assert spend.transaction_ids == tuple(row.id for row in outflows)
    assert earnings.typical_amount == Decimal("600")
    assert earnings.transaction_ids == tuple(row.id for row in inflows)


def test_variable_bill_has_wider_signed_band() -> None:
    amounts = ("-85", "-120", "-95", "-125", "-85", "-115")
    rows = _transactions("Invented waterworks", [_month(i, 12) for i in range(6)], amounts)
    (series,) = detect_recurring(rows, as_of=END)
    assert series.is_variable
    assert series.typical_amount == Decimal("-105")
    assert series.amount_band == (Decimal("-141.75"), Decimal("-68.25"))


@pytest.mark.parametrize("new_amount", ["-20.01", "-21.60", "-36"])
def test_price_rise_continues_series_and_updates_typical_amount(new_amount: str) -> None:
    amounts = ("-20",) * 7 + (new_amount,) * 2
    rows = _transactions("Invented archive", [_month(i, 15) for i in range(9)], amounts)
    (series,) = detect_recurring(rows, as_of=END)
    assert series.transaction_ids == tuple(row.id for row in rows)
    assert series.typical_amount == Decimal(new_amount)
    assert not series.is_variable


def test_annual_pair_has_lower_confidence_than_monthly_triplet() -> None:
    annual = _transactions("Invented registry", [_month(1, 29), _month(13, 29)])
    monthly = _transactions("Invented club", [_month(i, 8) for i in range(3)])
    assert detect_recurring(monthly[:2], as_of=END) == ()
    (monthly_series, annual_series) = detect_recurring(annual + monthly, as_of=END)
    assert annual_series.cadence == Cadence.ANNUAL
    assert annual_series.next_expected_date == _month(25, 28)
    assert Decimal("0") < annual_series.confidence < monthly_series.confidence


def test_empty_input() -> None:
    assert detect_recurring([], as_of=END) == ()
