"""Coverage and overlap candidates for statement imports and connector rows (ADR 0035)."""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.domain.fingerprint import normalize_description
from app.domain.ledger_rules import validate_amount

OVERLAP_WINDOW_DAYS = 3


@dataclass(frozen=True, slots=True)
class CoverageRun:
    period_start: date | None
    period_end: date | None
    reconcile_status: str
    posted_dates: tuple[date, ...]


@dataclass(frozen=True, slots=True, order=True)
class CoverageInterval:
    start_date: date
    end_date: date


def coverage(
    adopted_runs: Iterable[CoverageRun], closed_twin_runs: Iterable[CoverageRun] = ()
) -> tuple[CoverageInterval, ...]:
    """Compute the adopted account's fixed coverage from its runs and those of closed twins.

    An adopted run without usable bounds contributes no interval. A closed twin's run with rows
    always does, widened to its rows. Overlaps include a shared boundary day. Separate intervals,
    including adjacent ones, keep their own bounds.
    """
    intervals: list[CoverageInterval] = []
    for run in adopted_runs:
        if run.reconcile_status != "reconciled":
            continue
        start = run.period_start or min(run.posted_dates, default=None)
        if start is not None and run.period_end is not None and start <= run.period_end:
            intervals.append(CoverageInterval(start, run.period_end))
    for run in closed_twin_runs:
        # Every period a twin has rows for stays import-authoritative (ADR 0035 section 1), so
        # the interval widens to the run's rows and a missing period_end falls back to them.
        if not run.posted_dates:
            continue
        first, last = min(run.posted_dates), max(run.posted_dates)
        intervals.append(
            CoverageInterval(
                min(run.period_start, first) if run.period_start else first,
                max(run.period_end, last) if run.period_end else last,
            )
        )

    merged: list[CoverageInterval] = []
    for interval in sorted(intervals):
        if merged and interval.start_date <= merged[-1].end_date:
            previous = merged[-1]
            merged[-1] = CoverageInterval(
                previous.start_date, max(previous.end_date, interval.end_date)
            )
        else:
            merged.append(interval)
    return tuple(merged)


def is_covered(
    posted_date: date, intervals: Iterable[CoverageInterval], *, is_pending: bool = False
) -> bool:
    """Check a connector row against inclusive coverage using its posted date."""
    return not is_pending and any(i.start_date <= posted_date <= i.end_date for i in intervals)


@dataclass(frozen=True, slots=True)
class ImportCandidate:
    """Projection of a ledger row whose source is import."""

    id: UUID
    account_id: UUID
    amount: Decimal
    posted_date: date
    import_fingerprint: str
    description_raw: str = dc_field(repr=False)
    is_pending: bool = False
    in_supersession: bool = False
    arrived_after_adoption: bool = False

    def __post_init__(self) -> None:
        validate_amount(self.amount)


@dataclass(frozen=True, slots=True)
class ConnectorCandidate:
    id: UUID
    account_id: UUID
    amount: Decimal
    posted_date: date
    external_id: str
    description_raw: str = dc_field(repr=False)
    source: str
    connection_env: str
    authorized_date: date | None = None
    is_pending: bool = False
    in_supersession: bool = False

    def __post_init__(self) -> None:
        validate_amount(self.amount)

    @property
    def effective_date(self) -> date:
        return self.authorized_date or self.posted_date


@dataclass(frozen=True, slots=True)
class SupersedePair:
    import_id: UUID
    connector_id: UUID
    held: bool


def _description_tokens(description: str) -> set[str]:
    return {
        token
        for token in normalize_description(description).split()
        if not any(char.isdigit() for char in token)
    }


def is_held(
    import_description: str,
    connector_description: str,
    *,
    import_has_alternative: bool,
    connector_has_alternative: bool,
) -> bool:
    """Hold an ambiguous pair when its descriptions share no digit-free token."""
    return (import_has_alternative or connector_has_alternative) and _description_tokens(
        import_description
    ).isdisjoint(_description_tokens(connector_description))


def _has_alternative(day: int, candidates: Sequence[int], matched_index: int) -> bool:
    # The paired date is in range. If any other sorted date is in range, a neighbor is too.
    return (matched_index > 0 and day - candidates[matched_index - 1] <= OVERLAP_WINDOW_DAYS) or (
        matched_index + 1 < len(candidates)
        and candidates[matched_index + 1] - day <= OVERLAP_WINDOW_DAYS
    )


def match(
    imports: Iterable[ImportCandidate],
    connectors: Iterable[ConnectorCandidate],
    *,
    coverage_by_account: Mapping[UUID, Sequence[CoverageInterval]] | None = None,
) -> tuple[SupersedePair, ...]:
    """Find maximum-cardinality ordered pairs in each account and exact-amount group.

    Pass one candidate per ledger row. Set ``in_supersession`` for either side of an existing
    supersession, including its live winner. Each held pair consumes both candidates in this
    pass. Callers control when matching runs. The ledger writer validates transfer conflicts
    and checks current row state before applying a pair.
    """
    account_coverage = coverage_by_account if coverage_by_account is not None else {}
    import_groups: dict[tuple[UUID, Decimal], list[ImportCandidate]] = defaultdict(list)
    connector_groups: dict[tuple[UUID, Decimal], list[ConnectorCandidate]] = defaultdict(list)
    for row in imports:
        if row.is_pending or row.in_supersession:
            continue
        if row.arrived_after_adoption or not is_covered(
            row.posted_date, account_coverage.get(row.account_id, ())
        ):
            import_groups[row.account_id, row.amount].append(row)
    for connector in connectors:
        if (
            connector.is_pending
            or connector.in_supersession
            or connector.source not in {"plaid", "simplefin"}
            or connector.connection_env != "production"
            or is_covered(connector.posted_date, account_coverage.get(connector.account_id, ()))
        ):
            continue
        connector_groups[connector.account_id, connector.amount].append(connector)

    pairs: list[SupersedePair] = []
    for key in sorted(import_groups.keys() & connector_groups.keys()):
        pairs.extend(_match_group(import_groups[key], connector_groups[key]))
    return tuple(pairs)


def _match_group(
    imports: list[ImportCandidate], connectors: list[ConnectorCandidate]
) -> list[SupersedePair]:
    imports.sort(key=lambda row: (row.posted_date, row.import_fingerprint, row.id))
    connectors.sort(key=lambda row: (row.effective_date, row.posted_date, row.external_id))
    import_days = [row.posted_date.toordinal() for row in imports]
    connector_days = [row.effective_date.toordinal() for row in connectors]
    pairs: list[SupersedePair] = []
    p = q = 0
    while p < len(imports) and q < len(connectors):
        import_day, connector_day = import_days[p], connector_days[q]
        if abs(import_day - connector_day) <= OVERLAP_WINDOW_DAYS:
            held = is_held(
                imports[p].description_raw,
                connectors[q].description_raw,
                import_has_alternative=_has_alternative(import_day, connector_days, q),
                connector_has_alternative=_has_alternative(connector_day, import_days, p),
            )
            pairs.append(SupersedePair(imports[p].id, connectors[q].id, held))
            p += 1
            q += 1
        elif import_day < connector_day:
            p += 1
        else:
            q += 1
    return pairs
