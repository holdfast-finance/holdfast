"""Detect recurring posted transactions for one household without I/O.

Schedule analysis is CPU-bound. Callers must run it off the event loop.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from statistics import median
from uuid import UUID

from app.domain.money import ZERO

# Fixed charges allow minor fees without accepting broadly scattered purchases.
FIXED_AMOUNT_TOLERANCE = Decimal("0.02")
# Two postings distinguish a new price level from a single exceptional amount.
PRICE_CHANGE_CONFIRMATION = 2
# Weekly charges and payroll can move around weekends.
WEEK_DATE_TOLERANCE = 2
# Calendar billing tolerates a weekend or short holiday delay.
CALENDAR_DATE_TOLERANCE = 3
# A single skipped payment is allowed in an otherwise regular history.
MAX_CONSECUTIVE_MISSES = 1
# Six weekly postings distinguish bills from short bursts of shopping.
MIN_WEEKLY_OCCURRENCES = 6
# Five biweekly postings reduce chance matches on alternating weeks.
MIN_BIWEEKLY_OCCURRENCES = 5
# Six semi-monthly postings cover roughly three months of billing.
MIN_SEMI_MONTHLY_OCCURRENCES = 6
# Three monthly postings establish a repeat.
MIN_OCCURRENCES = 3
# Three quarterly postings distinguish recurrence from a single quarter-to-quarter repeat.
MIN_QUARTERLY_OCCURRENCES = 3
# Two annual payments can be the entire available history.
MIN_ANNUAL_OCCURRENCES = 2
# Broad amount bands need more observations than a tight fixed band.
MIN_VARIABLE_OCCURRENCES = 6
# A dozen fast variable postings distinguish bills from short shopping streaks.
MIN_FAST_VARIABLE_OCCURRENCES = 12
# Dense variable schedules need stronger evidence to distinguish regular shopping.
MIN_FAST_VARIABLE_SLOT_COVERAGE = Decimal("0.95")
# An annual pair has too little timing evidence to support a broad amount band.
ANNUAL_PAIR_AMOUNT_TOLERANCE = Decimal("0.001")
# Allow year-to-year utility changes while looking for the same seasonal pattern.
SEASONAL_AMOUNT_TOLERANCE = Decimal("0.25")
# A repeating annual shape needs most months to agree before relaxing raw coverage.
MIN_SEASONAL_PAIRS = 8
MIN_SEASONAL_TRANSACTION_COVERAGE = Decimal("0.65")
_CENT = Decimal("0.01")
# Three of four expected dates must be present, even across isolated misses.
MIN_SLOT_COVERAGE = Decimal("0.75")
# Frequent unmatched purchases in the same window weaken the cadence evidence.
MIN_TRANSACTION_COVERAGE = Decimal("0.80")
# Two payments supply materially less evidence than a longer history.
ANNUAL_PAIR_CONFIDENCE_CAP = Decimal("0.60")
_ANALYSIS_CONTEXT = Context(prec=40)


class Cadence(StrEnum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    SEMI_MONTHLY = "semi-monthly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


_MINIMUM = {
    Cadence.WEEKLY: MIN_WEEKLY_OCCURRENCES,
    Cadence.BIWEEKLY: MIN_BIWEEKLY_OCCURRENCES,
    Cadence.SEMI_MONTHLY: MIN_SEMI_MONTHLY_OCCURRENCES,
    Cadence.MONTHLY: MIN_OCCURRENCES,
    Cadence.QUARTERLY: MIN_QUARTERLY_OCCURRENCES,
    Cadence.ANNUAL: MIN_ANNUAL_OCCURRENCES,
}
_FAST_CADENCES = {Cadence.WEEKLY, Cadence.BIWEEKLY, Cadence.SEMI_MONTHLY}
_SEMI_MONTHLY_DAYS = ((1, 15), (15, 31), *((day, day + 15) for day in range(1, 17)))


@dataclass(frozen=True, slots=True)
class PostedTransaction:
    id: str | UUID
    account_id: str | UUID
    posted_date: date
    amount: Decimal
    merchant_key: str
    is_transfer: bool = False


@dataclass(frozen=True, slots=True)
class RecurringSeries:
    merchant_key: str
    account_id: str | UUID
    cadence: Cadence
    typical_amount: Decimal
    amount_band: tuple[Decimal, Decimal]
    is_variable: bool
    transaction_ids: tuple[str | UUID, ...]
    last_date: date
    next_expected_date: date
    confidence: Decimal
    lapsed: bool


@dataclass(frozen=True, slots=True)
class _Schedule:
    cadence: Cadence
    period: int
    phase: int
    days: tuple[int, ...] = ()

    @property
    def tolerance(self) -> int:
        return CALENDAR_DATE_TOLERANCE if self.days else WEEK_DATE_TOLERANCE

    def expected(self, slot: int) -> date:
        if not self.days:
            return date.fromordinal(slot * self.period + self.phase)
        cycle, position = divmod(slot, len(self.days))
        year, month = divmod(cycle * self.period + self.phase, 12)
        year += 1
        month += 1
        return date(year, month, min(self.days[position], monthrange(year, month)[1]))


@dataclass(frozen=True, slots=True)
class _Match:
    slot: int
    error: int
    transaction: PostedTransaction


def _date_votes(posted: date) -> tuple[tuple[_Schedule, int, int], ...]:
    """Index only schedules within posting tolerance of an observed date."""
    votes: list[tuple[_Schedule, int, int]] = []
    ordinal = posted.toordinal()
    for offset in range(-CALENDAR_DATE_TOLERANCE, CALENDAR_DATE_TOLERANCE + 1):
        expected_ordinal = ordinal + offset
        if not 1 <= expected_ordinal <= date.max.toordinal():
            continue
        error = abs(offset)
        if error <= WEEK_DATE_TOLERANCE:
            for cadence, period in ((Cadence.WEEKLY, 7), (Cadence.BIWEEKLY, 14)):
                slot, phase = divmod(expected_ordinal, period)
                votes.append((_Schedule(cadence, period, phase), slot, error))
        expected = posted + timedelta(days=offset)
        month = (expected.year - 1) * 12 + expected.month - 1
        month_end = monthrange(expected.year, expected.month)[1]
        for day in range(expected.day, 32 if expected.day == month_end else expected.day + 1):
            for cadence, period in (
                (Cadence.MONTHLY, 1),
                (Cadence.QUARTERLY, 3),
                (Cadence.ANNUAL, 12),
            ):
                slot, phase = divmod(month, period)
                votes.append((_Schedule(cadence, period, phase, (day,)), slot, error))
            for days in _SEMI_MONTHLY_DAYS:
                if day in days:
                    schedule = _Schedule(Cadence.SEMI_MONTHLY, 1, 0, days)
                    votes.append((schedule, month * 2 + days.index(day), error))
    return tuple(votes)


def _runs(slots: dict[int, list[_Match]], minimum: int) -> Iterator[list[list[_Match]]]:
    # Only score consecutive observed gaps, allowing a single skipped slot.
    if len(slots) < minimum:
        return
    run: list[list[_Match]] = []
    previous = -1
    for slot in sorted(slots):
        if run and slot - previous > MAX_CONSECUTIVE_MISSES + 1:
            if len(run) >= minimum:
                yield run
            run = []
        run.append(slots[slot])
        previous = slot
    if len(run) >= minimum:
        yield run


def _band(center: Decimal, tolerance: Decimal) -> tuple[Decimal, Decimal]:
    radius = abs(center) * tolerance
    return center - radius, center + radius


@dataclass(frozen=True, slots=True)
class _AmountProfile:
    center: Decimal
    band: tuple[Decimal, Decimal]
    is_variable: bool
    coverage_bands: tuple[tuple[Decimal, Decimal], ...]


def _fixed_profile(
    center: Decimal, tolerance: Decimal, previous: Decimal | None = None
) -> _AmountProfile:
    band = _band(center, tolerance)
    bands = (band,) if previous is None else (_band(previous, tolerance), band)
    return _AmountProfile(center, band, False, bands)


def _amount_profile(
    amounts: list[Decimal], minimum: int, annual_pair: bool
) -> _AmountProfile | None:
    center = median(amounts)
    tolerance = ANNUAL_PAIR_AMOUNT_TOLERANCE if annual_pair else FIXED_AMOUNT_TOLERANCE
    if all(amount == center for amount in amounts):
        return _fixed_profile(center, tolerance)
    low, high = _band(center, tolerance)
    fixed_count = sum(low <= amount <= high for amount in amounts)

    # Only consider splits whose final two postings could share a tight price level.
    if len(amounts) >= 2 * PRICE_CHANGE_CONFIRMATION:
        new = median(amounts[-PRICE_CHANGE_CONFIRMATION:])
        new_low, new_high = _band(new, tolerance)
        if all(new_low <= amount <= new_high for amount in amounts[-PRICE_CHANGE_CONFIRMATION:]):
            for split in range(
                len(amounts) - PRICE_CHANGE_CONFIRMATION, PRICE_CHANGE_CONFIRMATION - 1, -1
            ):
                old, new = median(amounts[:split]), median(amounts[split:])
                old_low, old_high = _band(old, tolerance)
                new_low, new_high = _band(new, tolerance)
                if (
                    old != new
                    and (
                        fixed_count < len(amounts)
                        or (
                            all(amount == old for amount in amounts[:split])
                            and all(amount == new for amount in amounts[split:])
                        )
                    )
                    and all(old_low <= amount <= old_high for amount in amounts[:split])
                    and all(new_low <= amount <= new_high for amount in amounts[split:])
                ):
                    return _fixed_profile(new, tolerance, old)
                if not new_low <= amounts[split - 1] <= new_high:
                    break

    # Keep occasional exceptions in the cadence while excluding them from the fixed price.
    if fixed_count == len(amounts):
        return _fixed_profile(center, tolerance)
    if fixed_count >= minimum and len(amounts) - fixed_count <= len(amounts) // 5:
        return _fixed_profile(
            median([amount for amount in amounts if low <= amount <= high]), tolerance
        )

    if len(amounts) < MIN_VARIABLE_OCCURRENCES:
        return None
    ordered = sorted(amounts)
    midpoint = len(ordered) // 2
    lower = median(ordered[:midpoint])
    upper = median(ordered[-midpoint:])
    fence = (upper - lower) * Decimal("1.5")
    inliers = [amount for amount in ordered if lower - fence <= amount <= upper + fence]
    if len(inliers) < max(minimum, MIN_VARIABLE_OCCURRENCES, len(amounts) - len(amounts) // 5):
        return None
    band = (inliers[0], inliers[-1])
    return _AmountProfile(median(inliers), band, True, (band,))


def _seasonal_matches(slots: list[list[_Match]]) -> dict[int, _Match]:
    """Find corroborating amounts in the same calendar month in adjacent years."""
    if len(slots) < 12 + MIN_SEASONAL_PAIRS:
        return {}
    by_slot = {matches[0].slot: matches for matches in slots}
    paired: dict[int, _Match] = {}
    pair_count = 0
    for slot, matches in by_slot.items():
        following = by_slot.get(slot + 12)
        if following is None:
            continue
        # Prefer candidates with corroborating amounts over whichever hit the billing date exactly.
        first, second = min(
            (
                (first, second)
                for first in ([paired[slot]] if slot in paired else matches)
                for second in following
            ),
            key=lambda pair: (
                abs(pair[0].transaction.amount - pair[1].transaction.amount),
                pair[0].error + pair[1].error,
            ),
        )
        change = abs(first.transaction.amount - second.transaction.amount)
        if change <= abs(first.transaction.amount) * SEASONAL_AMOUNT_TOLERANCE:
            paired[slot] = first
            paired[slot + 12] = second
            pair_count += 1
    if pair_count < MIN_SEASONAL_PAIRS or len(paired) * 3 < len(slots) * 2:
        return {}
    return paired


@dataclass(frozen=True, slots=True)
class _Candidate:
    series: RecurringSeries
    missed: int
    error: int


def _candidate(
    schedule: _Schedule,
    slots: list[list[_Match]],
    group: list[PostedTransaction],
    dates: list[date],
    as_of: date,
) -> _Candidate | None:
    count = len(slots)
    minimum = _MINIMUM[schedule.cadence]
    first_slot, last_slot = slots[0][0].slot, slots[-1][0].slot
    span = last_slot - first_slot + 1
    if count < span * MIN_SLOT_COVERAGE:
        return None

    seasonal = _seasonal_matches(slots) if schedule.cadence == Cadence.MONTHLY else {}
    run = [
        seasonal[matches[0].slot]
        if matches[0].slot in seasonal
        else min(matches, key=lambda match: match.error)
        for matches in slots
    ]
    annual_pair = schedule.cadence == Cadence.ANNUAL and count == MIN_ANNUAL_OCCURRENCES
    profile = _amount_profile([member.transaction.amount for member in run], minimum, annual_pair)
    if annual_pair and profile is None and any(len(matches) > 1 for matches in slots):
        # With only two slots, an unrelated posting can erase all the amount evidence.
        pair = min(
            ((first, last) for first in slots[0] for last in slots[1]),
            key=lambda pair: (
                abs(pair[0].transaction.amount - pair[1].transaction.amount)
                / abs(pair[0].transaction.amount + pair[1].transaction.amount),
                pair[0].error + pair[1].error,
            ),
        )
        profile = _amount_profile(
            [match.transaction.amount for match in pair], minimum, annual_pair
        )
    if profile is None or (
        profile.is_variable
        and schedule.cadence in _FAST_CADENCES
        and (
            count < MIN_FAST_VARIABLE_OCCURRENCES
            or Decimal(count) / span < MIN_FAST_VARIABLE_SLOT_COVERAGE
        )
    ):
        return None

    def in_band(amount: Decimal) -> bool:
        return any(low <= amount <= high for low, high in profile.coverage_bands)

    # Prefer an in-band posting when an ad-hoc purchase lands in the same slot.
    run = [
        min(
            matches,
            key=lambda match: (
                not in_band(match.transaction.amount),
                match != seasonal.get(match.slot),
                match.error,
            ),
        )
        for matches in slots
    ]
    first, last = run[0], run[-1]
    try:
        start = bisect_left(dates, schedule.expected(first.slot - 1))
    except ValueError:
        start = 0
    try:
        next_date = schedule.expected(last.slot + 1)
        lapse_date = schedule.expected(last.slot + 2)
    except ValueError:
        return None
    end = bisect_right(dates, next_date)
    matched = sum(in_band(member.transaction.amount) for member in run)
    nearby = sum(in_band(row.amount) for row in group[start:end])
    if matched < minimum:
        return None
    transaction_coverage = Decimal(matched) / nearby
    required_coverage = (
        MIN_SEASONAL_TRANSACTION_COVERAGE
        if seasonal and profile.is_variable
        else MIN_TRANSACTION_COVERAGE
    )
    if transaction_coverage < required_coverage:
        return None

    slot_coverage = Decimal(count) / span
    error = sum(member.error for member in run)
    date_fit = 1 - Decimal(error) / (count * (schedule.tolerance + 1))
    confidence = slot_coverage * transaction_coverage * (1 + date_fit) / 2
    if annual_pair:
        confidence = min(confidence, ANNUAL_PAIR_CONFIDENCE_CAP)

    series = RecurringSeries(
        merchant_key=first.transaction.merchant_key,
        account_id=first.transaction.account_id,
        cadence=schedule.cadence,
        typical_amount=profile.center.quantize(_CENT, rounding=ROUND_HALF_EVEN),
        amount_band=(
            profile.band[0].quantize(_CENT, rounding=ROUND_HALF_EVEN),
            profile.band[1].quantize(_CENT, rounding=ROUND_HALF_EVEN),
        ),
        is_variable=profile.is_variable,
        transaction_ids=tuple(member.transaction.id for member in run),
        last_date=last.transaction.posted_date,
        next_expected_date=next_date,
        confidence=confidence,
        lapsed=as_of > lapse_date,
    )
    return _Candidate(series, span - count, error)


def detect_recurring(
    transactions: Iterable[PostedTransaction], *, as_of: date
) -> tuple[RecurringSeries, ...]:
    """Return recurring series from postings on or before as_of.

    Group by merchant, account and sign. Competing schedules prefer more members, fewer missed
    dates and smaller posting offsets, in that order. Amount outliers stay in transaction_ids
    but are excluded from the typical amount. Each posting belongs to at most one series.
    Two misses end a series. A resumed schedule forms a separate series and callers must
    ignore or merge the older, lapsed duplicate.
    """
    groups: dict[tuple[str, str | UUID, bool], list[PostedTransaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.is_transfer or transaction.posted_date > as_of or not transaction.amount:
            continue
        merchant = transaction.merchant_key.strip().casefold()
        if not merchant:
            continue
        key = merchant, transaction.account_id, transaction.amount > ZERO
        groups[key].append(transaction)

    result: list[RecurringSeries] = []
    votes_by_date: dict[date, tuple[tuple[_Schedule, int, int], ...]] = {}
    with localcontext(_ANALYSIS_CONTEXT):
        for key in sorted(
            groups, key=lambda item: (item[0], str(item[1]), type(item[1]).__name__, item[2])
        ):
            group = groups[key]
            if len(group) < MIN_ANNUAL_OCCURRENCES:
                continue
            group.sort(key=lambda item: (item.posted_date, str(item.id), type(item.id).__name__))
            dates = [item.posted_date for item in group]
            schedules: dict[_Schedule, dict[int, list[_Match]]] = {}
            for transaction in group:
                day = transaction.posted_date
                if day not in votes_by_date:
                    votes_by_date[day] = _date_votes(day)
                for schedule, slot, error in votes_by_date[day]:
                    if len(group) >= _MINIMUM[schedule.cadence]:
                        schedules.setdefault(schedule, {}).setdefault(slot, []).append(
                            _Match(slot, error, transaction)
                        )
            candidates = [
                candidate
                for schedule, slots in schedules.items()
                for run in _runs(slots, _MINIMUM[schedule.cadence])
                if (candidate := _candidate(schedule, run, group, dates, as_of)) is not None
            ]
            used: set[str | UUID] = set()
            for candidate in sorted(
                candidates,
                key=lambda item: (
                    -len(item.series.transaction_ids),
                    item.missed,
                    item.error,
                    item.series.next_expected_date,
                ),
            ):
                series = candidate.series
                if used.isdisjoint(series.transaction_ids):
                    result.append(series)
                    used.update(series.transaction_ids)
    return tuple(result)
