"""Detect recurring posted transactions for one household without I/O."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from calendar import monthrange
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from enum import StrEnum
from statistics import median
from uuid import UUID

from app.domain.money import ZERO

# Minor changes in fixed charges can come from fees or billing adjustments.
FIXED_AMOUNT_TOLERANCE = Decimal("0.15")
# Utilities and similar bills can fluctuate appreciably between cycles.
VARIABLE_AMOUNT_TOLERANCE = Decimal("0.35")
# A smaller price change needs two tightly clustered price levels.
PRICE_LEVEL_TOLERANCE = Decimal("0.02")
# A repeated price change should exceed minor variation within a level.
MIN_PRICE_CHANGE = Decimal("0.05")
# A single new amount is weaker evidence than two postings at the new price.
PRICE_CHANGE_CONFIRMATION = 2
# Payroll and weekly charges can move around weekends.
WEEK_DATE_TOLERANCE = 2
# Calendar billing tolerates a weekend or short holiday delay.
CALENDAR_DATE_TOLERANCE = 3
# A single skipped payment is allowed in an otherwise regular history.
MAX_CONSECUTIVE_MISSES = 1
# Three observations distinguish a repeat from an isolated pair.
MIN_OCCURRENCES = 3
# Two annual payments can be the entire available history.
MIN_ANNUAL_OCCURRENCES = 2
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

    def match(self, day: date) -> tuple[int, int] | None:
        if self.days:
            month = (day.year - 1) * 12 + day.month - 1
            base = ((month - self.phase) // self.period) * len(self.days)
        else:
            base = (day.toordinal() - self.phase) // self.period
        nearest: tuple[int, int] | None = None
        for slot in range(base - 1, base + max(1, len(self.days)) + 1):
            try:
                error = abs((self.expected(slot) - day).days)
            except ValueError:
                continue
            if error <= self.tolerance and (nearest is None or error < nearest[1]):
                nearest = slot, error
        return nearest


def _schedules() -> Iterator[_Schedule]:
    for cadence, period in ((Cadence.WEEKLY, 7), (Cadence.BIWEEKLY, 14)):
        for phase in range(period):
            yield _Schedule(cadence, period, phase)
    for first in range(1, 17):
        yield _Schedule(Cadence.SEMI_MONTHLY, 1, 0, (first, first + 15))
    yield _Schedule(Cadence.SEMI_MONTHLY, 1, 0, (15, 31))
    for cadence, period in ((Cadence.MONTHLY, 1), (Cadence.QUARTERLY, 3), (Cadence.ANNUAL, 12)):
        for phase in range(period):
            for day in range(1, 32):
                yield _Schedule(cadence, period, phase, (day,))


@dataclass(frozen=True, slots=True)
class _Match:
    slot: int
    error: int
    transaction: PostedTransaction


def _runs(schedule: _Schedule, transactions: Sequence[PostedTransaction]) -> Iterator[list[_Match]]:
    # Keep one posting per scheduled date, favoring the closest posting.
    slots: dict[int, _Match] = {}
    for transaction in transactions:
        match = schedule.match(transaction.posted_date)
        if match is None:
            continue
        slot, error = match
        if slot not in slots or error < slots[slot].error:
            slots[slot] = _Match(slot, error, transaction)

    run: list[_Match] = []
    for slot in sorted(slots):
        if run and slot - run[-1].slot > MAX_CONSECUTIVE_MISSES + 1:
            yield run
            run = []
        run.append(slots[slot])
    if run:
        yield run


def _fits(amounts: Sequence[Decimal], center: Decimal, tolerance: Decimal) -> bool:
    return all(abs(amount - center) <= abs(center) * tolerance for amount in amounts)


def _amount_profile(amounts: list[Decimal]) -> tuple[Decimal, Decimal] | None:
    center = median(amounts)
    if all(amount == center for amount in amounts):
        return center, FIXED_AMOUNT_TOLERANCE
    fixed = _fits(amounts, center, FIXED_AMOUNT_TOLERANCE)
    # Detect one price change. For a small shift, require tighter clusters to avoid
    # mistaking ordinary variable bills for a new fixed price.
    level_tolerance = PRICE_LEVEL_TOLERANCE if fixed else FIXED_AMOUNT_TOLERANCE
    for split in range(len(amounts) - 1, MIN_OCCURRENCES - 2, -1):
        before, after = amounts[:split], amounts[split:]
        old, new = median(before), median(after)
        shift = abs(new - old) / abs(old)
        if (
            shift > ZERO
            and (
                shift >= MIN_PRICE_CHANGE
                or (
                    len(after) >= PRICE_CHANGE_CONFIRMATION
                    and _fits(before, old, ZERO)
                    and _fits(after, new, ZERO)
                )
            )
            and (len(after) >= PRICE_CHANGE_CONFIRMATION or shift <= VARIABLE_AMOUNT_TOLERANCE)
            and _fits(before, old, level_tolerance)
            and _fits(after, new, level_tolerance)
        ):
            return new, FIXED_AMOUNT_TOLERANCE
    if fixed:
        return center, FIXED_AMOUNT_TOLERANCE
    if _fits(amounts, center, VARIABLE_AMOUNT_TOLERANCE):
        return center, VARIABLE_AMOUNT_TOLERANCE
    return None


@dataclass(frozen=True, slots=True)
class _Candidate:
    series: RecurringSeries
    missed: int
    error: int


def _candidate(
    schedule: _Schedule, run: list[_Match], dates: list[date], as_of: date
) -> _Candidate | None:
    count = len(run)
    minimum = MIN_ANNUAL_OCCURRENCES if schedule.cadence == Cadence.ANNUAL else MIN_OCCURRENCES
    if count < minimum:
        return None
    first, last = run[0], run[-1]
    span = last.slot - first.slot + 1
    slot_coverage = Decimal(count) / span
    if slot_coverage < MIN_SLOT_COVERAGE:
        return None

    # Include one surrounding cycle so a short, coincidental run cannot hide nearby purchases.
    try:
        start = bisect_left(dates, schedule.expected(first.slot - 1))
    except ValueError:
        start = 0
    end = bisect_right(dates, schedule.expected(last.slot + 1))
    transaction_coverage = Decimal(count) / (end - start)
    if transaction_coverage < MIN_TRANSACTION_COVERAGE:
        return None

    profile = _amount_profile([member.transaction.amount for member in run])
    if profile is None:
        return None
    center, tolerance = profile
    radius = abs(center) * tolerance
    error = sum(member.error for member in run)
    date_fit = 1 - Decimal(error) / (count * (schedule.tolerance + 1))
    confidence = slot_coverage * transaction_coverage * (1 + date_fit) / 2
    if count == MIN_ANNUAL_OCCURRENCES:
        confidence = min(confidence, ANNUAL_PAIR_CONFIDENCE_CAP)

    series = RecurringSeries(
        merchant_key=first.transaction.merchant_key,
        account_id=first.transaction.account_id,
        cadence=schedule.cadence,
        typical_amount=center,
        amount_band=(center - radius, center + radius),
        is_variable=tolerance == VARIABLE_AMOUNT_TOLERANCE,
        transaction_ids=tuple(member.transaction.id for member in run),
        last_date=last.transaction.posted_date,
        next_expected_date=schedule.expected(last.slot + 1),
        confidence=confidence,
        lapsed=as_of > schedule.expected(last.slot + 2),
    )
    return _Candidate(series, span - count, error)


def detect_recurring(
    transactions: Iterable[PostedTransaction], *, as_of: date
) -> tuple[RecurringSeries, ...]:
    """Return recurring series from postings on or before as_of.

    Group by merchant, account and sign. Competing schedules prefer more members, fewer missed
    dates and smaller posting offsets, in that order. Each posting belongs to at most one series.
    """
    groups: dict[tuple[str, str | UUID, bool], list[PostedTransaction]] = defaultdict(list)
    for transaction in transactions:
        if transaction.is_transfer or transaction.posted_date > as_of or not transaction.amount:
            continue
        key = transaction.merchant_key, transaction.account_id, transaction.amount > ZERO
        groups[key].append(transaction)

    result: list[RecurringSeries] = []
    schedules = tuple(_schedules())
    with localcontext(_ANALYSIS_CONTEXT):
        for key in sorted(
            groups, key=lambda item: (item[0], str(item[1]), type(item[1]).__name__, item[2])
        ):
            group = groups[key]
            if len(group) < MIN_ANNUAL_OCCURRENCES:
                continue
            group.sort(key=lambda item: (item.posted_date, str(item.id), type(item.id).__name__))
            dates = [item.posted_date for item in group]
            candidates = [
                candidate
                for schedule in schedules
                for run in _runs(schedule, group)
                if (candidate := _candidate(schedule, run, dates, as_of)) is not None
            ]
            used: set[str | UUID] = set()
            for candidate in sorted(
                candidates,
                key=lambda item: (-len(item.series.transaction_ids), item.missed, item.error),
            ):
                series = candidate.series
                if used.isdisjoint(series.transaction_ids):
                    result.append(series)
                    used.update(series.transaction_ids)
    return tuple(result)
