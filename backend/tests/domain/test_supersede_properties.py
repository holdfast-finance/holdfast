"""Properties of the ADR 0035 overlap graph, using synthetic dates and amounts."""

from dataclasses import replace
from random import Random
from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from app.domain.supersede import ConnectorCandidate, ImportCandidate, match
from tests.domain.test_supersede import connected, imported

DAY_OFFSETS = st.integers(min_value=0, max_value=10)
SMALL_GROUP = st.lists(DAY_OFFSETS, max_size=6)


def _brute_force_size(import_days: list[int], connector_days: list[int]) -> int:
    """Explore every eligible assignment, including leaving an import unmatched."""
    if not import_days:
        return 0
    first, *remaining = import_days
    best = _brute_force_size(remaining, connector_days)
    for index, connector_day in enumerate(connector_days):
        if abs(first - connector_day) <= 3:
            other_connectors = connector_days[:index] + connector_days[index + 1 :]
            best = max(best, 1 + _brute_force_size(remaining, other_connectors))
    return best


@given(SMALL_GROUP, SMALL_GROUP, st.randoms())
def test_single_pass_is_maximum_and_deterministic(
    import_days: list[int], connector_days: list[int], rng: Random
) -> None:
    imports = [imported(i + 1, day) for i, day in enumerate(import_days)]
    # Posted order differs from effective order for rows with an authorized date.
    connectors = [
        replace(connected(i + 11, day + 4, authorized=day), description_raw="ATM")
        if i % 2
        else connected(i + 11, day)
        for i, day in enumerate(connector_days)
    ]
    pairs = match(imports, connectors)
    assert len(pairs) == _brute_force_size(import_days, connector_days)
    import_indices = {row.id: i for i, row in enumerate(imports)}
    connector_indices = {row.id: i for i, row in enumerate(connectors)}
    rng.shuffle(imports)
    rng.shuffle(connectors)
    assert pairs == match(imports, connectors)
    used_imports = [import_indices[pair.import_id] for pair in pairs]
    used_connectors = [connector_indices[pair.connector_id] for pair in pairs]
    assert len(set(used_imports)) == len(set(used_connectors)) == len(pairs)
    paired_import_days = [import_days[i] for i in used_imports]
    paired_connector_days = [connector_days[i] for i in used_connectors]
    assert paired_import_days == sorted(paired_import_days)
    assert paired_connector_days == sorted(paired_connector_days)
    assert all(
        abs(old - new) <= 3
        for old, new in zip(paired_import_days, paired_connector_days, strict=True)
    )
    for pair, old_day, new_day in zip(
        pairs, paired_import_days, paired_connector_days, strict=True
    ):
        alternatives = (
            sum(abs(old_day - day) <= 3 for day in connector_days) > 1
            or sum(abs(new_day - day) <= 3 for day in import_days) > 1
        )
        assert pair.held == (connector_indices[pair.connector_id] % 2 == 1 and alternatives)


@given(st.lists(st.tuples(st.booleans(), SMALL_GROUP), max_size=8))
def test_no_residue_after_interleaved_connector_batches_and_statement_blocks(
    batches: list[tuple[bool, list[int]]],
) -> None:
    imports: list[ImportCandidate] = []
    connectors: list[ConnectorCandidate] = []
    used_import_ids: set[UUID] = set()
    used_connector_ids: set[UUID] = set()
    for index, (is_connector_batch, days) in enumerate(batches, start=1):
        if is_connector_batch:
            connectors.extend(connected(index * 10 + i, day) for i, day in enumerate(days))
        else:
            imports.extend(imported(index * 10 + i, day) for i, day in enumerate(days))
        for pair in match(imports, connectors):
            assert not pair.held  # These descriptions share a token.
            assert pair.import_id not in used_import_ids
            assert pair.connector_id not in used_connector_ids
            used_import_ids.add(pair.import_id)
            used_connector_ids.add(pair.connector_id)
        imports = [replace(row, in_supersession=row.id in used_import_ids) for row in imports]
        connectors = [
            replace(row, in_supersession=row.id in used_connector_ids) for row in connectors
        ]
        assert all(
            abs((new.effective_date - old.posted_date).days) > 3
            for old in imports
            for new in connectors
            if not old.in_supersession and not new.in_supersession
        )
        assert match(imports, connectors) == ()


@given(SMALL_GROUP, st.lists(SMALL_GROUP, max_size=5))
def test_effective_date_ordered_batches_preserve_the_live_multiset(
    import_days: list[int],
    batches: list[list[int]],
) -> None:
    # Prefix splits preserve the single-pass walk when all imports are already known.
    # The named earlier-date regression covers arbitrary batch order.
    imports = [imported(i + 1, day) for i, day in enumerate(import_days)]
    connectors = [
        connected(i + 11, day)
        for i, day in enumerate(sorted(day for batch in batches for day in batch))
    ]
    all_at_once = {pair.import_id for pair in match(imports, connectors)}
    unmatched = imports.copy()
    offset = 0
    for batch in batches:
        end = offset + len(batch)
        matched = {pair.import_id for pair in match(unmatched, connectors[offset:end])}
        unmatched = [row for row in unmatched if row.id not in matched]
        offset = end
    # All connector rows stay live in both executions.
    assert [row for row in imports if row.id not in all_at_once] == unmatched
