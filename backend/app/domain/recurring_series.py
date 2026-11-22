"""Plan recurring-series updates and derive their read-time status and tier."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal
from uuid import UUID

from app.domain.recurring import (
    CALENDAR_DATE_TOLERANCE,
    WEEK_DATE_TOLERANCE,
    Cadence,
    PostedTransaction,
    RecurringSeries,
    detect_recurring,
)

Direction = Literal["outflow", "inflow"]
UserState = Literal["auto", "confirmed", "dismissed"]
Status = Literal["active", "late", "possibly_cancelled"]
Tier = Literal["bill", "possible"]

NOMINAL_DAYS = {
    Cadence.WEEKLY: 7,
    Cadence.BIWEEKLY: 14,
    Cadence.SEMI_MONTHLY: 15,
    Cadence.MONTHLY: 30,
    Cadence.QUARTERLY: 91,
    Cadence.ANNUAL: 365,
}
CONFIDENCE_STEP = Decimal("0.001")
_BILL_THRESHOLD = Decimal("0.60")
_SHORT_CADENCES = (Cadence.WEEKLY, Cadence.BIWEEKLY, Cadence.SEMI_MONTHLY)


def stored_confidence(raw: Decimal) -> Decimal:
    return raw.quantize(CONFIDENCE_STEP, rounding=ROUND_HALF_EVEN)


def series_status(cadence: Cadence, next_expected_date: date, as_of: date) -> Status:
    tolerance = (
        WEEK_DATE_TOLERANCE
        if cadence in (Cadence.WEEKLY, Cadence.BIWEEKLY)
        else CALENDAR_DATE_TOLERANCE
    )
    days_past_due = (as_of - next_expected_date).days
    if days_past_due <= tolerance:
        return "active"
    if days_past_due > NOMINAL_DAYS[cadence] // 2:
        return "possibly_cancelled"
    return "late"


def series_tier(
    confidence: Decimal, cadence: Cadence, is_variable: bool, user_state: UserState
) -> Tier:
    if user_state == "confirmed" or (
        stored_confidence(confidence) > _BILL_THRESHOLD
        and not (is_variable and cadence in _SHORT_CADENCES)
    ):
        return "bill"
    return "possible"


@dataclass(frozen=True, slots=True)
class DetectedSeries:
    series: RecurringSeries
    direction: Direction
    first_date: date
    member_ids: frozenset[UUID]


def detected_from(series: RecurringSeries, posted: Mapping[UUID, date]) -> DetectedSeries:
    members = frozenset(
        UUID(member) if isinstance(member, str) else member for member in series.transaction_ids
    )
    direction: Direction = "outflow" if series.typical_amount.is_signed() else "inflow"
    return DetectedSeries(series, direction, min(posted[member] for member in members), members)


@dataclass(frozen=True, slots=True)
class StoredSeries:
    id: UUID
    account_id: UUID
    merchant_key: str
    direction: Direction
    cadence: Cadence
    last_date: date
    lapsed: bool
    user_state: UserState
    label: str | None
    member_ids: frozenset[UUID]
    newest_member_id: UUID | None
    all_members_transfers: bool

    @property
    def user_stated(self) -> bool:
        return self.user_state != "auto" or self.label is not None


@dataclass(frozen=True, slots=True)
class PlannedSeries:
    stored_id: UUID | None
    detected: DetectedSeries | None
    member_ids: frozenset[UUID]
    takes_state_from: UUID | None


@dataclass(frozen=True, slots=True)
class SeriesPlan:
    upserts: tuple[PlannedSeries, ...]
    deletes: tuple[UUID, ...]


def _overlap_edges(
    stored: Mapping[UUID, StoredSeries], detection_by_member: Mapping[UUID, int]
) -> list[tuple[int, bool, UUID, int]]:
    edges = []
    for series in stored.values():
        counts = Counter(
            detection_by_member[member]
            for member in series.member_ids
            if member in detection_by_member
        )
        newest = (
            detection_by_member.get(series.newest_member_id)
            if series.newest_member_id is not None
            else None
        )
        for index, count in counts.items():
            # Among split detections, only the one with the newest live member can inherit the id.
            if len(counts) > 1 and newest is not None and index != newest:
                continue
            edges.append((-count, not series.user_stated, series.id, index))
    return sorted(edges)


def reconcile_series(
    stored: Sequence[StoredSeries], detected: Sequence[DetectedSeries]
) -> SeriesPlan:
    """Reconcile disjoint stored memberships with disjoint detector output for one household."""
    detections = sorted(detected, key=lambda item: (item.first_date, min(item.member_ids)))
    detection_by_member = {
        member: index for index, item in enumerate(detections) for member in item.member_ids
    }
    stored_by_id = {series.id: series for series in stored if not series.all_members_transfers}
    edges = _overlap_edges(stored_by_id, detection_by_member)

    matches: dict[int, UUID] = {}
    matched: set[UUID] = set()
    for _, _, candidate_id, index in edges:
        if candidate_id not in matched and index not in matches:
            matches[index] = candidate_id
            matched.add(candidate_id)

    takeovers: set[UUID] = set()
    displaced: set[UUID] = set()
    for negative_count, is_auto, candidate_id, index in edges:
        incumbent = matches.get(index)
        if (
            not is_auto
            and candidate_id not in matched
            and incumbent is not None
            and not stored_by_id[incumbent].user_stated
            and -negative_count * 2 > len(stored_by_id[candidate_id].member_ids)
        ):
            matches[index] = candidate_id
            matched.remove(incumbent)
            matched.add(candidate_id)
            takeovers.add(candidate_id)
            displaced.add(incumbent)

    predecessors: dict[tuple[UUID | str, str, Direction, Cadence], list[StoredSeries]] = (
        defaultdict(list)
    )
    for series in sorted(
        stored_by_id.values(), key=lambda item: (-item.last_date.toordinal(), item.id)
    ):
        if series.lapsed and series.id not in matched and series.id not in displaced:
            key = series.account_id, series.merchant_key, series.direction, series.cadence
            predecessors[key].append(series)

    upserts = []
    for index, item in enumerate(detections):
        stored_id = matches.get(index)
        members = item.member_ids
        if stored_id is None:
            detected_key = (
                item.series.account_id,
                item.series.merchant_key,
                item.direction,
                item.series.cadence,
            )
            for predecessor in predecessors.get(detected_key, ()):
                if predecessor.id not in matched and predecessor.last_date < item.first_date:
                    stored_id = predecessor.id
                    matched.add(stored_id)
                    members |= predecessor.member_ids.difference(detection_by_member)
                    break
        state_source = stored_id if stored_id in takeovers else None
        upserts.append(PlannedSeries(stored_id, item, members, state_source))

    deletes = []
    for series in sorted(stored, key=lambda item: item.id):
        if series.id in matched:
            continue
        if series.all_members_transfers or not series.user_stated:
            deletes.append(series.id)
        else:
            members = series.member_ids.difference(detection_by_member)
            upserts.append(PlannedSeries(series.id, None, members, None))
    return SeriesPlan(tuple(upserts), tuple(deletes))


RowTuple = tuple[str, str, str, str, str]


def detect_rows(rows: Sequence[RowTuple]) -> tuple[DetectedSeries, ...]:
    """Decode the worker's live, posted, non-transfer rows and detect as of the UTC date."""
    transactions = []
    posted = {}
    for txn_id, account_id, day, amount, key in rows:
        member_id = UUID(txn_id)
        posted_date = date.fromisoformat(day)
        posted[member_id] = posted_date
        transactions.append(
            PostedTransaction(member_id, UUID(account_id), posted_date, Decimal(amount), key)
        )
    found = detect_recurring(transactions, as_of=datetime.now(UTC).date())
    return tuple(detected_from(series, posted) for series in found)
