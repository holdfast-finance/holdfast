from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.domain.recurring import Cadence, RecurringSeries
from app.domain.recurring_series import (
    DetectedSeries,
    Direction,
    PlannedSeries,
    RowTuple,
    SeriesPlan,
    StoredSeries,
    UserState,
    detect_rows,
    detected_from,
    reconcile_series,
    series_status,
    series_tier,
    stored_confidence,
)

ACCOUNT = UUID(int=1000)
FIRST = date(2024, 1, 10)
LAST = date(2023, 10, 10)
KEY = "invented studio"


def _ids(members: range | tuple[int, ...]) -> frozenset[UUID]:
    return frozenset(UUID(int=member) for member in members)


def _stored(
    series_id: int,
    members: range | tuple[int, ...],
    *,
    state: UserState = "auto",
    label: str | None = None,
    last_date: date = LAST,
    lapsed: bool = False,
    transfers: bool = False,
) -> StoredSeries:
    member_ids = _ids(members)
    return StoredSeries(
        UUID(int=series_id),
        ACCOUNT,
        KEY,
        "outflow",
        Cadence.MONTHLY,
        last_date,
        lapsed,
        state,
        label,
        member_ids,
        max(member_ids, default=None),
        transfers,
    )


def _detected(
    members: range | tuple[int, ...], *, key: str = KEY, first: date = FIRST
) -> DetectedSeries:
    member_ids = _ids(members)
    series = RecurringSeries(
        key,
        ACCOUNT,
        Cadence.MONTHLY,
        Decimal("-21.20"),
        (Decimal("-22.00"), Decimal("-20.00")),
        False,
        tuple(sorted(member_ids)),
        first + timedelta(days=60),
        first + timedelta(days=90),
        Decimal("0.9504"),
        False,
    )
    return DetectedSeries(series, "outflow", first, member_ids)


@pytest.mark.parametrize(
    ("cadence", "tolerance", "half_interval"),
    [
        (Cadence.WEEKLY, 2, 3),
        (Cadence.BIWEEKLY, 2, 7),
        (Cadence.SEMI_MONTHLY, 3, 7),
        (Cadence.MONTHLY, 3, 15),
        (Cadence.QUARTERLY, 3, 45),
        (Cadence.ANNUAL, 3, 182),
    ],
)
def test_status_boundaries(cadence: Cadence, tolerance: int, half_interval: int) -> None:
    assert series_status(cadence, FIRST, FIRST - timedelta(days=1)) == "active"
    assert series_status(cadence, FIRST, FIRST + timedelta(days=tolerance)) == "active"
    assert series_status(cadence, FIRST, FIRST + timedelta(days=tolerance + 1)) == "late"
    assert series_status(cadence, FIRST, FIRST + timedelta(days=half_interval)) == "late"
    assert (
        series_status(cadence, FIRST, FIRST + timedelta(days=half_interval + 1))
        == "possibly_cancelled"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", "0.000"),
        ("0.6004", "0.600"),
        ("0.6005", "0.600"),
        ("0.6006", "0.601"),
        ("0.6015", "0.602"),
        ("0.9999", "1.000"),
    ],
)
def test_stored_confidence_rounds_half_even(raw: str, expected: str) -> None:
    assert stored_confidence(Decimal(raw)).as_tuple() == Decimal(expected).as_tuple()


@pytest.mark.parametrize("cadence", list(Cadence))
def test_bill_tier_threshold_and_user_override(cadence: Cadence) -> None:
    assert (
        series_tier(Decimal("0.6004"), cadence, False, "auto")
        == series_tier(Decimal("0.600"), cadence, False, "auto")
        == "possible"
    )
    assert series_tier(Decimal("0.601"), cadence, False, "auto") == "bill"
    assert series_tier(Decimal("0"), cadence, True, "confirmed") == "bill"
    assert series_tier(Decimal("0"), cadence, True, "dismissed") == "possible"
    assert series_tier(Decimal("0.601"), cadence, False, "dismissed") == "bill"


@pytest.mark.parametrize(
    ("cadence", "tier"),
    [
        (Cadence.WEEKLY, "possible"),
        (Cadence.BIWEEKLY, "possible"),
        (Cadence.SEMI_MONTHLY, "possible"),
        (Cadence.MONTHLY, "bill"),
        (Cadence.QUARTERLY, "bill"),
        (Cadence.ANNUAL, "bill"),
    ],
)
def test_high_confidence_variable_tier_depends_on_cadence(cadence: Cadence, tier: str) -> None:
    assert series_tier(Decimal("0.950"), cadence, True, "auto") == tier


@given(
    confidence=st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=6),
    cadence=st.sampled_from(list(Cadence)),
    variable=st.booleans(),
    state=st.sampled_from(("auto", "confirmed", "dismissed")),
)
def test_tier_survives_confidence_round_trip(
    confidence: Decimal, cadence: Cadence, variable: bool, state: UserState
) -> None:
    assert series_tier(confidence, cadence, variable, state) == series_tier(
        stored_confidence(confidence), cadence, variable, state
    )


@pytest.mark.parametrize(
    ("amount", "direction"),
    [("-21.20", "outflow"), ("21.20", "inflow"), ("0.00", "outflow"), ("-0.00", "outflow")],
)
def test_detected_from_derives_direction_first_date_and_uuid_members(
    amount: str, direction: Direction
) -> None:
    first, last = UUID(int=3), UUID(int=1)
    series = replace(
        _detected((1, 3)).series,
        typical_amount=Decimal(amount),
        transaction_ids=(last, str(first)),
    )
    item = detected_from(series, {first: FIRST, last: series.last_date, UUID(int=2): LAST})
    assert item == DetectedSeries(series, direction, FIRST, frozenset({first, last}))


def test_detected_from_normalises_a_string_account_id_for_the_lapsed_merge_key() -> None:
    series = replace(_detected((1, 2)).series, account_id=str(ACCOUNT))
    item = detected_from(series, {UUID(int=1): FIRST, UUID(int=2): LAST})
    assert item.series.account_id == ACCOUNT
    assert isinstance(item.series.account_id, UUID)
    predecessor = _stored(1, (7, 8), lapsed=True, last_date=date(2023, 1, 1))
    plan = reconcile_series([predecessor], [item])
    assert [(p.stored_id, p.member_ids) for p in plan.upserts] == [
        (predecessor.id, _ids((1, 2, 7, 8)))
    ]


def test_detect_rows_decodes_the_worker_payload_and_uses_the_detector() -> None:
    rows: list[RowTuple] = [
        (
            str(UUID(int=month + offset)),
            str(account),
            date(2024, month, 10).isoformat(),
            amount,
            KEY,
        )
        for offset, account, amount in (
            (0, ACCOUNT, "-21.20"),
            (3, ACCOUNT, "1425.25"),
            (6, UUID(int=1001), "-19.89"),
        )
        for month in range(1, 4)
    ]

    with patch("app.domain.recurring_series.datetime") as clock:
        clock.now.return_value = datetime(2024, 6, 1, tzinfo=UTC)
        found = detect_rows(rows)
        clock.now.assert_called_once_with(UTC)
        assert detect_rows(rows[::-1]) == found
    assert {item.member_ids for item in found} == {
        _ids((1, 2, 3)),
        _ids((4, 5, 6)),
        _ids((7, 8, 9)),
    }
    assert {
        (item.series.account_id, item.direction, item.series.typical_amount) for item in found
    } == {
        (ACCOUNT, "outflow", Decimal("-21.20")),
        (ACCOUNT, "inflow", Decimal("1425.25")),
        (UUID(int=1001), "outflow", Decimal("-19.89")),
    }
    assert all(item.first_date == FIRST and item.series.lapsed for item in found)
    assert all(item.series.cadence == Cadence.MONTHLY for item in found)
    assert all(item.series.next_expected_date == date(2024, 4, 10) for item in found)
    assert all(frozenset(item.series.transaction_ids) == item.member_ids for item in found)
    assert detect_rows([]) == ()


@pytest.mark.parametrize(
    ("state", "label"),
    [("confirmed", None), ("dismissed", None), ("auto", "Studio membership")],
)
def test_overlap_tie_prefers_user_stated_then_lowest_id(
    state: UserState, label: str | None
) -> None:
    low = _stored(1, (1, 2))
    preferred = _stored(2, (3, 4), state=state, label=label)
    high = _stored(3, (5, 6), state=state, label=label)
    detection = _detected(range(1, 7), key="renamed studio")
    assert reconcile_series([high, low, preferred], [detection]) == SeriesPlan(
        (
            PlannedSeries(preferred.id, detection, detection.member_ids, None),
            PlannedSeries(high.id, None, frozenset(), None),
        ),
        (low.id,),
    )


def test_greedy_matching_prefers_larger_overlap_and_uses_next_available_edge() -> None:
    first = replace(_stored(1, range(1, 7)), newest_member_id=None)
    second = _stored(2, (7, 8))
    left = _detected((1, 2, 3, 4, 7, 8))
    right = _detected((5, 6))
    assert reconcile_series([second, first], [right, left]) == SeriesPlan(
        (
            PlannedSeries(first.id, left, left.member_ids, None),
            PlannedSeries(None, right, right.member_ids, None),
        ),
        (second.id,),
    )
    stronger = _stored(3, range(7, 12))
    left = _detected((1, 2, 3, 4, 7, 8, 9, 10, 11))
    assert reconcile_series([first, stronger], [left, right]).upserts == (
        PlannedSeries(stronger.id, left, left.member_ids, None),
        PlannedSeries(first.id, right, right.member_ids, None),
    )


@pytest.mark.parametrize(
    ("state", "label"),
    [("confirmed", "Studio membership"), ("dismissed", None), ("auto", "Studio membership")],
)
def test_user_stated_series_takes_over_an_auto_match_on_strict_majority(
    state: UserState, label: str | None
) -> None:
    auto = _stored(1, (1, 2, 3, 4), lapsed=True)
    donor = _stored(2, (5, 6, 7, 8), state=state, label=label)
    detection = _detected(range(1, 8))
    other = _detected((9, 10, 11))
    assert reconcile_series([auto, donor], [detection, other]) == SeriesPlan(
        (
            PlannedSeries(donor.id, detection, _ids(range(1, 9)), donor.id),
            PlannedSeries(None, other, other.member_ids, None),
        ),
        (auto.id,),
    )


@pytest.mark.parametrize(
    ("incumbent_state", "taken"),
    [("auto", (5, 6)), ("confirmed", (5, 6, 7)), ("dismissed", (5, 6, 7))],
)
def test_takeover_requires_majority_and_an_auto_incumbent(
    incumbent_state: UserState, taken: tuple[int, ...]
) -> None:
    incumbent = _stored(1, (1, 2, 3, 4), state=incumbent_state)
    donor = _stored(2, (5, 6, 7, 8), state="confirmed")
    detection = _detected((1, 2, 3, 4, *taken))
    assert reconcile_series([incumbent, donor], [detection]) == SeriesPlan(
        (
            PlannedSeries(incumbent.id, detection, detection.member_ids, None),
            PlannedSeries(donor.id, None, donor.member_ids - detection.member_ids, None),
        ),
        (),
    )


def test_newest_twelve_members_carry_the_confirmed_id_to_the_new_merchant_key() -> None:
    saved = _stored(1, range(1, 31), state="confirmed", label="Studio membership")
    history = _detected(range(1, 19))
    current = _detected(range(19, 31), key="invented studio autopay")
    assert reconcile_series([saved], [current, history]) == SeriesPlan(
        (
            PlannedSeries(None, history, history.member_ids, None),
            PlannedSeries(saved.id, current, current.member_ids, None),
        ),
        (),
    )


def test_split_series_falls_back_when_its_newest_detection_goes_to_a_larger_overlap() -> None:
    confirmed = _stored(1, (*range(1, 11), 30), state="confirmed")
    twin = _stored(2, range(11, 30))
    old_history = _detected(range(1, 11))
    current = _detected(range(11, 31), first=date(2024, 3, 1))
    plan = reconcile_series([confirmed, twin], [current, old_history])
    assert plan == SeriesPlan(
        (
            PlannedSeries(confirmed.id, old_history, old_history.member_ids, None),
            PlannedSeries(twin.id, current, current.member_ids, None),
        ),
        (),
    )
    assert all(item.detected is not None and item.member_ids for item in plan.upserts)


def test_merge_uses_latest_lapsed_predecessor_then_lowest_id_and_unions_history() -> None:
    older = _stored(1, (1, 2, 3), lapsed=True, last_date=LAST - timedelta(days=10))
    chosen = _stored(2, (4, 5, 6), state="dismissed", lapsed=True)
    tied = _stored(3, (7, 8, 9), lapsed=True)
    detection = _detected((10, 11, 12))

    assert reconcile_series([older, chosen, tied], [detection]) == SeriesPlan(
        (PlannedSeries(chosen.id, detection, chosen.member_ids | detection.member_ids, None),),
        (older.id, tied.id),
    )


def test_rerun_after_a_lapsed_merge_keeps_the_predecessor_history() -> None:
    old = _stored(1, (1, 2, 3), state="confirmed", lapsed=True)
    resumed = _detected((4, 5, 6))
    merged = PlannedSeries(old.id, resumed, _ids(range(1, 7)), None)
    assert reconcile_series([old], [resumed]) == SeriesPlan((merged,), ())

    stored = replace(old, lapsed=False, member_ids=merged.member_ids, newest_member_id=UUID(int=6))
    assert reconcile_series([stored], [resumed]) == SeriesPlan((merged,), ())


def test_matched_series_keeps_members_no_detection_claims() -> None:
    labelled = _stored(1, (1, 2, 3, 4), label="Studio membership")
    other = _stored(2, (5, 6, 7))
    detection = _detected((3, 4, 8))
    claims_two = _detected((2, 5, 6, 7), first=FIRST - timedelta(days=1))
    plan = reconcile_series([labelled, other], [detection, claims_two])
    assert plan == SeriesPlan(
        (
            PlannedSeries(other.id, claims_two, claims_two.member_ids, None),
            PlannedSeries(labelled.id, detection, _ids((1, 3, 4, 8)), None),
        ),
        (),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"account_id": UUID(int=1001)},
        {"merchant_key": "invented print club"},
        {"direction": "inflow"},
        {"cadence": Cadence.QUARTERLY},
        {"last_date": FIRST},
        {"last_date": FIRST + timedelta(days=1)},
        {"lapsed": False},
    ],
)
def test_lapsed_merge_requires_exact_group_and_strictly_earlier_predecessor(
    changes: dict[str, object],
) -> None:
    old = replace(_stored(1, (1, 2, 3), lapsed=True), **changes)
    detection = _detected((4, 5, 6))
    assert reconcile_series([old], [detection]) == SeriesPlan(
        (PlannedSeries(None, detection, detection.member_ids, None),), (old.id,)
    )


def test_lapsed_merge_retains_only_history_unclaimed_by_other_detections() -> None:
    old = _stored(1, (1, 2, 3), state="confirmed", lapsed=True)
    other = _stored(2, (4, 5, 6))
    history = _detected((3, 4, 5, 6))
    resumed = _detected((7, 8, 9))
    assert reconcile_series([old, other], [resumed, history]) == SeriesPlan(
        (
            PlannedSeries(other.id, history, history.member_ids, None),
            PlannedSeries(old.id, resumed, _ids((1, 2, 7, 8, 9)), None),
        ),
        (),
    )


@pytest.mark.parametrize("state", ["auto", "confirmed", "dismissed"])
def test_all_transfer_series_is_deleted_even_if_it_could_inherit_a_detection(
    state: UserState,
) -> None:
    stored = _stored(
        1, (1, 2, 3), state=state, label="Invented payment", transfers=True, lapsed=True
    )
    matched = _detected((1, 2, 3))
    resumed = _detected((4, 5, 6))
    assert reconcile_series([stored], [matched, resumed]) == SeriesPlan(
        (
            PlannedSeries(None, matched, matched.member_ids, None),
            PlannedSeries(None, resumed, resumed.member_ids, None),
        ),
        (stored.id,),
    )
    assert reconcile_series([stored], []) == SeriesPlan((), (stored.id,))


def test_no_detections_keeps_user_choices_and_removes_unlabelled_auto() -> None:
    auto = _stored(1, (1, 2, 3))
    confirmed = _stored(2, (), state="confirmed")
    dismissed = _stored(3, (4, 5, 6), state="dismissed")
    labelled = _stored(4, (7, 8, 9), label="Studio membership")
    assert reconcile_series([labelled, auto, confirmed, dismissed], []) == SeriesPlan(
        tuple(
            PlannedSeries(item.id, None, item.member_ids, None)
            for item in (confirmed, dismissed, labelled)
        ),
        (auto.id,),
    )
    assert reconcile_series([], []) == SeriesPlan((), ())


@given(
    membership=st.lists(st.tuples(st.integers(0, 4), st.integers(0, 4)), max_size=30),
    states=st.lists(
        st.tuples(
            st.sampled_from(("auto", "confirmed", "dismissed")),
            st.booleans(),
            st.booleans(),
            st.booleans(),
        ),
        min_size=4,
        max_size=4,
    ),
    data=st.data(),
)
def test_reconciliation_invariants(
    membership: list[tuple[int, int]],
    states: list[tuple[UserState, bool, bool, bool]],
    data: st.DataObject,
) -> None:
    stored = [
        _stored(
            group,
            tuple(i for i, (old, _) in enumerate(membership, start=1) if old == group),
            state=state,
            label="Studio membership" if labelled else None,
            lapsed=lapsed,
            transfers=transfers,
        )
        for group, (state, labelled, lapsed, transfers) in enumerate(states, start=1)
    ]
    detected = [
        _detected(members)
        for group in range(1, 5)
        if (members := tuple(i for i, (_, new) in enumerate(membership, start=1) if new == group))
    ]
    plan = reconcile_series(stored, detected)

    assert plan == reconcile_series(
        data.draw(st.permutations(stored)), data.draw(st.permutations(detected))
    )
    assert Counter(item.detected for item in plan.upserts if item.detected is not None) == Counter(
        detected
    )
    assigned: set[UUID] = set()
    for item in plan.upserts:
        assert assigned.isdisjoint(item.member_ids)
        assigned.update(item.member_ids)
        if item.detected is not None:
            assert item.detected.member_ids <= item.member_ids
    assert Counter(item.stored_id for item in plan.upserts if item.stored_id is not None) + Counter(
        plan.deletes
    ) == Counter(item.id for item in stored)
    for item in stored:
        if item.all_members_transfers:
            assert item.id in plan.deletes
        elif item.user_state != "auto" or item.label is not None:
            assert item.id not in plan.deletes
