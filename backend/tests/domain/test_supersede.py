"""ADR 0035 examples using synthetic rows and a synthetic card statement."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from app.domain.ledger_rules import InvalidAmountError
from app.domain.statement_parse import parse_statement
from app.domain.supersede import (
    ConnectorCandidate,
    CoverageInterval,
    CoverageRun,
    ImportCandidate,
    SupersedePair,
    coverage,
    is_covered,
    is_held,
    match,
)
from app.importers.profile_loader import load_profiles

ACCOUNT = UUID(int=100)
OTHER_ACCOUNT = UUID(int=200)
DAY = date(2026, 1, 1)


def day(offset: int) -> date:
    return DAY + timedelta(days=offset)


def imported(row_id: int, offset: int = 0, amount: str = "-5.00") -> ImportCandidate:
    return ImportCandidate(
        UUID(int=row_id), ACCOUNT, Decimal(amount), day(offset), str(row_id), "Cafe"
    )


def connected(
    row_id: int, offset: int = 0, amount: str = "-5.00", *, authorized: int | None = None
) -> ConnectorCandidate:
    return ConnectorCandidate(
        UUID(int=row_id),
        ACCOUNT,
        Decimal(amount),
        day(offset),
        str(row_id),
        "CAFE",
        "plaid",
        "production",
        authorized_date=day(authorized) if authorized is not None else None,
    )


@pytest.mark.parametrize("has_authorization", [False, True])
def test_four_equal_daily_charges_pair_with_or_without_authorized_dates(
    has_authorization: bool,
) -> None:
    imports = [imported(i + 1, i) for i in range(4)]
    connectors = [
        connected(i + 11, i + 1, authorized=i if has_authorization else None) for i in range(4)
    ]
    assert match(imports, connectors) == tuple(
        SupersedePair(old.id, new.id, held=False)
        for old, new in zip(imports, connectors, strict=True)
    )


def test_two_coffees_pair_once_each_and_one_connector_only_pairs_once() -> None:
    first, second = imported(1), imported(2)
    coffee, another_coffee = connected(11), connected(12)
    assert match([second, first], [another_coffee, coffee]) == (
        SupersedePair(first.id, coffee.id, held=False),
        SupersedePair(second.id, another_coffee.id, held=False),
    )
    assert match([second, first], [coffee]) == (SupersedePair(first.id, coffee.id, held=False),)


def test_ties_use_import_fingerprint_then_id_and_connector_post_date_then_external_id() -> None:
    imports = [
        replace(imported(1), import_fingerprint="z"),
        replace(imported(3), import_fingerprint="a"),
        replace(imported(2), import_fingerprint="a"),
    ]
    connectors = [
        replace(connected(11, 2, authorized=0), external_id="a"),
        replace(connected(12, 1, authorized=0), external_id="z"),
        replace(connected(13, 1, authorized=0), external_id="b"),
    ]
    assert [(p.import_id.int, p.connector_id.int) for p in match(imports, connectors)] == [
        (2, 13),
        (3, 12),
        (1, 11),
    ]


def test_connector_effective_date_orders_before_posted_date() -> None:
    imports = [imported(1), imported(2, 1)]
    connectors = [connected(11, 4, authorized=0), connected(12, 2, authorized=1)]
    assert [(p.import_id.int, p.connector_id.int) for p in match(imports, connectors)] == [
        (1, 11),
        (2, 12),
    ]


@pytest.mark.parametrize("offset", [-4, -3, 3, 4])
@pytest.mark.parametrize("has_authorization", [False, True])
def test_symmetric_window_uses_authorized_date_when_available(
    offset: int, has_authorization: bool
) -> None:
    old = imported(1, 4)
    new = (
        connected(11, 5, authorized=4 + offset) if has_authorization else connected(11, 4 + offset)
    )
    expected = (SupersedePair(old.id, new.id, held=False),) if abs(offset) == 3 else ()
    assert match([old], [new]) == expected


def test_groups_use_account_and_exact_signed_amount() -> None:
    outflow, inflow = imported(1), imported(2, amount="5.00")
    exact_outflow, exact_inflow = connected(11, amount="-5.0"), connected(12, amount="5.00")
    assert match([outflow, inflow], [exact_inflow, exact_outflow]) == (
        SupersedePair(outflow.id, exact_outflow.id, held=False),
        SupersedePair(inflow.id, exact_inflow.id, held=False),
    )
    assert match([outflow], [connected(13, amount="-5.01"), exact_inflow]) == ()
    assert match([outflow], [replace(exact_outflow, account_id=OTHER_ACCOUNT)]) == ()


def test_candidates_require_valid_decimal_amounts() -> None:
    for candidate in (imported(1), connected(11)):
        for invalid in (cast(Decimal, -5), Decimal("NaN"), Decimal("-5.001")):
            with pytest.raises(InvalidAmountError):
                replace(candidate, amount=invalid)


def test_only_eligible_rows_match_or_count_as_alternatives() -> None:
    old = replace(imported(1), description_raw="TRANSFER")
    new = replace(connected(11), source="simplefin", description_raw="ATM")
    imports = [
        replace(old, id=UUID(int=2), is_pending=True),
        replace(old, id=UUID(int=3), in_supersession=True),
    ]
    connectors = [
        replace(connected(12), is_pending=True),
        replace(connected(13), in_supersession=True),
        replace(connected(14), connection_env="sandbox"),
        replace(connected(15), connection_env="development"),
        replace(connected(16), source="manual"),
    ]
    assert match(imports, [new]) == ()
    assert match([old], connectors) == ()
    assert match([*imports, old], [*connectors, new]) == (
        SupersedePair(old.id, new.id, held=False),
    )


@pytest.mark.parametrize(
    ("import_alternative", "connector_alternative", "connector_description", "expected"),
    [
        (False, False, "ATM BRANCH9", False),
        (True, False, "ATM BRANCH9", True),
        (False, True, "ATM BRANCH9", True),
        (True, True, "  transfer\tATM BRANCH9 ", False),
        (True, True, "", True),
    ],
)
def test_hold_requires_ambiguity_and_no_shared_digit_free_description_token(
    import_alternative: bool,
    connector_alternative: bool,
    connector_description: str,
    expected: bool,
) -> None:
    assert (
        is_held(
            "Transfer Branch9",
            connector_description,
            import_has_alternative=import_alternative,
            connector_has_alternative=connector_alternative,
        )
        is expected
    )


def test_held_pairs_consume_both_rows_and_include_earlier_pairs_as_alternatives() -> None:
    imports = [replace(imported(i), description_raw="TRANSFER") for i in (1, 2)]
    connectors = [replace(connected(i, 3), description_raw="ATM") for i in (11, 12)]
    assert match(imports, connectors) == (
        SupersedePair(imports[0].id, connectors[0].id, held=True),
        SupersedePair(imports[1].id, connectors[1].id, held=True),
    )
    assert match(imports[:1], [connectors[0], connected(13, 4)]) == (
        SupersedePair(imports[0].id, connectors[0].id, held=False),
    )


def test_coverage_merges_overlaps_preserves_gaps_and_includes_closed_twins_with_rows() -> None:
    runs = [
        CoverageRun(day(100), day(119), "reconciled", (day(101),)),
        CoverageRun(day(31), day(58), "accepted", (day(31),)),
        CoverageRun(day(0), day(30), "reconciled", (day(2),)),
        CoverageRun(day(30), day(30), "reconciled", (day(30),)),
        CoverageRun(None, day(100), "reconciled", (day(99), day(94))),
        CoverageRun(day(120), day(121), "reconciled", ()),
        CoverageRun(day(60), day(88), "needs_review", (day(60),)),
    ]
    twins = [
        CoverageRun(None, day(89), "accepted", (day(80), day(59))),
        CoverageRun(day(31), day(58), "reconciled", ()),
    ]
    expected = (
        CoverageInterval(day(0), day(30)),
        CoverageInterval(day(59), day(89)),
        CoverageInterval(day(94), day(119)),
        CoverageInterval(day(120), day(121)),
    )
    intervals = coverage(runs, twins)
    assert intervals == expected
    assert coverage(reversed(runs), reversed(twins)) == expected
    assert all(is_covered(day(offset), intervals) for offset in (0, 30, 59, 89))
    assert all(not is_covered(day(offset), intervals) for offset in (31, 45, 58, 90, 122))
    assert not is_covered(day(30), intervals, is_pending=True)


def test_coverage_skips_runs_without_usable_bounds() -> None:
    assert (
        coverage(
            [
                CoverageRun(day(1), None, "reconciled", (day(1),)),
                CoverageRun(None, day(1), "reconciled", ()),
                CoverageRun(None, day(1), "reconciled", (day(2),)),
            ]
        )
        == ()
    )


def test_matching_uses_posted_coverage_per_account_and_allows_later_imports() -> None:
    intervals = {ACCOUNT: (CoverageInterval(day(0), day(30)),)}
    old = imported(1, 30)
    posted_in_gap = connected(11, 31, authorized=30)
    posted_in_coverage = connected(12, 30, authorized=31)
    assert match([old], [posted_in_gap], coverage_by_account=intervals) == ()
    later_import = replace(old, arrived_after_adoption=True)
    assert match([later_import], [posted_in_coverage], coverage_by_account=intervals) == ()
    assert match(
        [later_import], [posted_in_coverage, posted_in_gap], coverage_by_account=intervals
    ) == (SupersedePair(old.id, posted_in_gap.id, held=False),)
    gap_import = imported(2, 32)
    assert match([gap_import], [posted_in_gap], coverage_by_account=intervals) == (
        SupersedePair(gap_import.id, posted_in_gap.id, held=False),
    )
    assert match(
        [replace(old, account_id=OTHER_ACCOUNT)],
        [replace(posted_in_coverage, account_id=OTHER_ACCOUNT)],
        coverage_by_account=intervals,
    ) == (SupersedePair(old.id, posted_in_coverage.id, held=False),)


def test_later_batch_with_earlier_effective_date_can_change_live_multiset() -> None:
    first, last = imported(1, 0), imported(2, 4)
    earlier, later = connected(11, 0), connected(12, 2)
    assert match([first, last], [earlier, later]) == (
        SupersedePair(first.id, earlier.id, held=False),
        SupersedePair(last.id, later.id, held=False),
    )
    assert match([first, last], [later]) == (SupersedePair(first.id, later.id, held=False),)
    assert (
        match(
            [replace(first, in_supersession=True), last],
            [earlier, replace(later, in_supersession=True)],
        )
        == ()
    )


def test_card_layout_transaction_dates_match_connector_authorizations_and_interest() -> None:
    fixture = Path(__file__).parents[1] / "fixtures/supersede/card-layout.sanitized.txt"
    profile = next(profile for profile in load_profiles() if profile.id == "navy-federal-card")
    statement = parse_statement(fixture.read_text(encoding="utf-8"), profile)
    (block,) = statement.blocks
    assert block.reconciliation.status == "reconciled"
    assert [t.posted_date for t in block.txns] == [
        date(2026, 4, 20),
        date(2026, 4, 21),
        date(2026, 4, 22),
        date(2026, 4, 23),
        statement.period_end,
    ]
    assert [t.amount for t in block.txns] == [Decimal("-5.00")] * 4 + [Decimal("-0.75")]
    imports = [
        ImportCandidate(
            UUID(int=i), ACCOUNT, row.amount, row.posted_date, row.fingerprint, row.description
        )
        for i, row in enumerate(block.txns, start=1)
    ]
    connectors = [
        replace(
            connected(i + 11),
            posted_date=row.posted_date + timedelta(days=i % 4),
            authorized_date=row.posted_date,
            amount=row.amount,
            description_raw=row.description_raw,
        )
        for i, row in enumerate(imports)
    ]
    pairs = match(imports, connectors)
    assert pairs == tuple(
        SupersedePair(old.id, new.id, held=False)
        for old, new in zip(imports, connectors, strict=True)
    )
    live_imports = {row.id for row in imports} - {pair.import_id for pair in pairs if not pair.held}
    assert not live_imports
