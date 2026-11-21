from __future__ import annotations

from calendar import monthrange
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from random import Random
from time import perf_counter

import pytest

from app.domain.recurring import Cadence, PostedTransaction, detect_recurring

START = date(2023, 1, 1)
END = date(2024, 12, 31)
SEASONAL_AMOUNTS = (
    "-60",
    "-70",
    "-90",
    "-120",
    "-180",
    "-210",
    "-190",
    "-110",
    "-80",
    "-65",
    "-60",
    "-75",
)
HOLDOUT_FALSE_SERIES_CAP = 5
PRECISION_FALSE_SERIES_CAP = 12


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
        days = ((15, 31), (5, 20), (1, 15))[variant % 3]
        return [_month(offset, day) for offset in range(24) for day in days]
    period = {Cadence.MONTHLY: 1, Cadence.QUARTERLY: 3, Cadence.ANNUAL: 12}[cadence]
    return [_month(offset, 28 + variant) for offset in range(variant % period, 24, period)]


def _clustered_amount(rng: Random, center: int, spread: int) -> str:
    # Sum two uniform draws in cents to make amounts concentrate around the usual spend.
    delta = rng.randint(-spread, spread) + rng.randint(-spread, spread)
    return str(Decimal(-(center + delta)).scaleb(-2))


@pytest.mark.parametrize("seed", [75109, 49297, 84053])
def test_holdout_recovers_planted_series_among_irregular_purchases(seed: int) -> None:
    rng = Random(seed)  # noqa: S311 -- reproducible synthetic fixture
    season = Random(seed + 1)  # noqa: S311 -- independent seasonal amount variation
    history: list[PostedTransaction] = []
    expected: dict[tuple[str, Cadence], set[str]] = {}
    shared: set[tuple[str, Cadence]] = set()
    shared_bills = set(rng.sample(range(36), 15))
    for index, cadence in enumerate(Cadence):
        for variant in range(6):
            dates = [day + timedelta(days=rng.randint(-2, 2)) for day in _dates(cadence, variant)]
            merchant = f"Invented bill {cadence} {variant}"
            amounts = (str(Decimal(-(5700 + variant * 1341)).scaleb(-2)),)
            if cadence == Cadence.MONTHLY and variant % 2 == 0:
                amounts = tuple(
                    str(Decimal(amount) * Decimal(season.randint(94, 106)).scaleb(-2))
                    for amount in SEASONAL_AMOUNTS * 2
                )
            if cadence in (Cadence.BIWEEKLY, Cadence.SEMI_MONTHLY) and variant % 2 == 0:
                amounts = ("1425.25",)
            if cadence == Cadence.QUARTERLY and variant == 2:
                amounts = ("-52", "-64", "-43", "-60")
            rows = _transactions(merchant, dates, amounts)
            if variant == 3 and len(rows) >= 8:
                rows.pop(4)
                rows[-2:] = [replace(row, amount=row.amount * Decimal("1.12")) for row in rows[-2:]]
            history.extend(rows)
            key = (merchant, cadence)
            expected[key] = {str(row.id) for row in rows}
            if index * 6 + variant in shared_bills:
                shared.add(key)
                count = rng.randint(3, 15)
                days = [START + timedelta(days=rng.randrange(731)) for _ in range(count)]
                purchases = _transactions(
                    f"{merchant} purchase",
                    days,
                    tuple(_clustered_amount(rng, 4400, 2000) for _ in days),
                )
                history.extend(replace(row, merchant_key=merchant) for row in purchases)

    for merchant, count, center, spread in [
        ("Invented groceries", 160, 8500, 3250),
        ("Invented gas", 77, 5700, 2100),
        ("Invented pharmacy", 37, 3300, 1500),
        ("Invented restaurant", 95, 5400, 2000),
        ("Invented kiosk", 29, 1500, 0),
        ("Invented craft stall", 17, 2750, 0),
        *[(f"Invented shop {i}", rng.randint(3, 12), 4500, 1800) for i in range(40)],
    ]:
        days = [START + timedelta(days=rng.randrange(731)) for _ in range(count)]
        amounts = tuple(_clustered_amount(rng, center, spread) for _ in days)
        history.extend(_transactions(merchant, days, amounts))

    market_days = [
        START + timedelta(days=offset + rng.randint(-1, 1))
        for offset in range(6, 730, 7)
        if rng.randrange(100) < 85
    ]
    history.extend(
        _transactions(
            "Invented weekly market",
            market_days,
            tuple(_clustered_amount(rng, 4600, 2050) for _ in market_days),
        )
    )
    history.extend(
        _transactions(
            "Invented daily coffee", [START + timedelta(days=i) for i in range(731)], ("-4.75",)
        )
    )

    rng.shuffle(history)
    # Include jittered postings for every planned cycle, including the year boundary.
    as_of = END + timedelta(days=2)
    found = detect_recurring(history, as_of=as_of)
    recovered: set[tuple[str, Cadence]] = set()
    false_series = []
    for series in found:
        key = (series.merchant_key, series.cadence)
        planted = expected.get(key, set())
        matched = len(planted.intersection(series.transaction_ids))
        # Require 90% membership recall and precision, counting each planted bill once.
        if (
            planted
            and key not in recovered
            and matched * 10 >= len(planted) * 9
            and matched * 10 >= len(series.transaction_ids) * 9
        ):
            recovered.add(key)
        else:
            false_series.append(
                (series.merchant_key, series.cadence, len(series.transaction_ids), matched)
            )
    print(
        f"Holdout {seed}: {len(recovered)}/{len(expected)} overall, "
        f"{len(recovered & shared)}/{len(shared)} shared, {len(false_series)} false"
    )
    assert len(recovered) * 100 >= len(expected) * 95, expected.keys() - recovered
    assert len(recovered & shared) * 100 >= len(shared) * 95, shared - recovered
    assert len(false_series) <= HOLDOUT_FALSE_SERIES_CAP, false_series
    assert all(Decimal("0") <= series.confidence <= Decimal("1") for series in found)

    rng.shuffle(history)
    assert detect_recurring(history, as_of=as_of) == found


def test_precision_on_twenty_thousand_random_rows() -> None:
    rng = Random(93671)  # noqa: S311 -- reproducible synthetic fixture
    start = date(2015, 1, 1)
    merchants = rng.choices(range(1500), weights=[1 / rank for rank in range(1, 1501)], k=20_000)
    rows = [
        PostedTransaction(
            f"noise-{index}",
            "example-checking",
            start + timedelta(days=rng.randrange(3652)),
            Decimal(-rng.randint(100, 20000)).scaleb(-2),
            f"Invented merchant {merchant}",
        )
        for index, merchant in enumerate(merchants)
    ]
    started = perf_counter()
    found = detect_recurring(rows, as_of=date(2024, 12, 31))
    elapsed = perf_counter() - started
    print(f"Precision: {len(found)} false on {len(rows)} rows in {elapsed:.2f}s")
    assert len(found) <= PRECISION_FALSE_SERIES_CAP


@pytest.mark.parametrize(
    ("amounts", "typical", "variable"),
    [
        (SEASONAL_AMOUNTS, "-85", True),
        (SEASONAL_AMOUNTS * 2, "-85", True),
        (("-20",) * 5 + ("-45",) + ("-20",) * 6, "-20", False),
        (("-20",) * 11 + ("-35",), "-20", False),
        (("-1500",) * 5 + ("-750",) + ("-1500",) * 6, "-1500", False),
        (("-20",) * 7 + ("-30",), "-20", False),
    ],
)
def test_monthly_amount_exceptions_preserve_members(
    amounts: tuple[str, ...], typical: str, variable: bool
) -> None:
    rows = _transactions("Invented bill", [_month(i, 10) for i in range(len(amounts))], amounts)
    (series,) = detect_recurring(rows, as_of=END)
    assert series.cadence == Cadence.MONTHLY
    assert series.transaction_ids == tuple(row.id for row in rows)
    assert series.typical_amount == Decimal(typical)
    assert series.is_variable is variable


@pytest.mark.parametrize(("days", "next_day"), [((1, 15), 1), ((15, 31), 15)])
def test_semi_monthly_calendar_anchors(days: tuple[int, int], next_day: int) -> None:
    rows = _transactions("Invented payroll", [_month(i, day) for i in range(3) for day in days])
    (series,) = detect_recurring(rows, as_of=END)
    assert series.cadence == Cadence.SEMI_MONTHLY
    assert series.next_expected_date == _month(3, next_day)


def test_merchant_grouping_and_half_even_rounding() -> None:
    rows = _transactions("Invented phone", [_month(i, 10) for i in range(3)], ("-10.005",))
    rows[0] = replace(rows[0], merchant_key="invented phone")
    blanks = _transactions("  ", [_month(i, 10) for i in range(3)])
    (series,) = detect_recurring(rows + blanks, as_of=END)
    assert series.transaction_ids == tuple(row.id for row in rows)
    assert series.typical_amount.as_tuple() == Decimal("-10.00").as_tuple()
    assert series.amount_band == (Decimal("-10.21"), Decimal("-9.80"))


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
    assert series.amount_band == (Decimal("-20.40"), Decimal("-19.60"))
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
    assert series.amount_band == (Decimal("-125.00"), Decimal("-85.00"))


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


def test_annual_pair_among_ad_hoc_purchases_including_same_day() -> None:
    bill_days = [_month(2, 15), _month(14, 15)]
    bills = _transactions("Invented registry", bill_days, ("-90",))
    purchases = _transactions(
        "Invented shop",
        [*bill_days, *(START + timedelta(days=i) for i in (3, 8, 25, 62, 77, 139, 205, 220))],
        ("-11.20", "-38.70", "-47.30", "-15", "-23", "-48", "-36", "-55", "-71", "-52"),
    )
    rows = bills + [
        replace(row, id=f"ad-hoc-{row.id}", merchant_key=bills[0].merchant_key) for row in purchases
    ]
    (series,) = detect_recurring(rows, as_of=END)
    assert series.cadence == Cadence.ANNUAL
    assert series.transaction_ids == tuple(row.id for row in bills)
    assert series.confidence == Decimal("0.60")


def test_empty_input() -> None:
    assert detect_recurring([], as_of=END) == ()
