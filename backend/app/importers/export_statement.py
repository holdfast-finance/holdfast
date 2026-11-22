"""Turn parsed CSV / OFX exports into the ``ParsedStatement`` the import service persists.

Pure (no I/O). One persistence path serves every source (ADR 0011), so an export is shaped like
a statement: a profile id, one block per account, the parser's own reconciliation verdict. Nothing
is re-derived here: a file with no balances stays ``needs_review`` with the parser's reason.
"""

from __future__ import annotations

from datetime import date

from app.domain.csv_parse import CsvProfile, parse_csv
from app.domain.ofx_parse import OfxStatement, parse_ofx
from app.domain.statement_parse import ParsedBlock, ParsedStatement, StatementParseError

OFX_PROFILE_ID = "ofx"
MAX_ROWS = 50_000  # per file; far above a real export, and bounds every later per-row query


def _span(dates: list[date], start: date | None, end: date | None) -> tuple[date | None, date]:
    """The statement period: declared dates, else the first and last posting."""
    if not dates:
        raise StatementParseError("no transactions")
    if len(dates) > MAX_ROWS:
        raise StatementParseError("too many rows")
    lo = start or min(dates)
    hi = end or max(dates)
    return min(lo, hi), hi


def csv_statement(
    source: bytes, profiles: list[tuple[str, CsvProfile]], kind: str = "bank"
) -> ParsedStatement | None:
    """Parse with the first profile whose header and rows fit, else ``None``."""
    for profile_id, profile in profiles:
        try:
            export = parse_csv(source, profile)
        except StatementParseError:
            continue
        if not export.txns:
            continue
        start, end = _span([t.posted_date for t in export.txns], None, None)
        block = ParsedBlock(
            key="main",
            account_last4=None,
            opening_balance=export.opening_balance,
            closing_balance=export.closing_balance,
            txns=tuple(export.txns),
            reconciliation=export.reconciliation,
        )
        return ParsedStatement(
            profile_id=profile_id,
            institution=f"Imported CSV ({profile_id.removeprefix('csv-')})",
            kind=kind,
            period_start=start,
            period_end=end,
            blocks=(block,),
        )
    return None


def ofx_statement(source: bytes, id_key: bytes) -> ParsedStatement:
    statements: tuple[OfxStatement, ...] = parse_ofx(source, id_key=id_key)
    kinds = {s.kind for s in statements}
    if len(kinds) != 1:
        raise StatementParseError("mixed bank and card statements")
    if any(s.currency != "USD" for s in statements):
        raise StatementParseError("only USD is supported")
    if len({(s.account_last4, s.currency) for s in statements}) != len(statements):
        raise StatementParseError("ambiguous accounts")
    blocks: list[ParsedBlock] = []
    starts: list[date] = []
    ends: list[date] = []
    for s in statements:
        lo, hi = _span([t.posted_date for t in s.txns], s.period_start, s.period_end)
        starts.append(lo or hi)
        ends.append(hi)
        blocks.append(
            ParsedBlock(
                key=s.account_token,
                account_last4=s.account_last4,
                opening_balance=s.opening_balance,
                closing_balance=s.closing_balance,
                txns=tuple(s.txns),
                reconciliation=s.reconciliation,
            )
        )
    return ParsedStatement(
        profile_id=OFX_PROFILE_ID,
        institution="OFX account",
        kind="card" if kinds == {"card"} else "bank",
        period_start=min(starts),
        period_end=max(ends),
        blocks=tuple(blocks),
    )
