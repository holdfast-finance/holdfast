"""Pure statement parsing: layout text + a bank profile -> a parsed, reconciled statement.

No I/O (ADR 0011). The caller turns a PDF into layout text (``app.importers.pdf_text``) and loads
profiles from YAML (``app.importers.profile_loader``); this module only matches regexes and does
``Decimal`` arithmetic. Amounts are returned in Holdfast's sign convention: outflow negative,
inflow positive, balances assets positive and amounts owed negative (hard rule 1).

A statement may hold several *blocks* (a bank statement listing checking and savings, or a deposit
statement with savings pods). Each block has its own opening/closing balance and reconciles on its
own: opening + sum(transactions) = closing within 0.01, else ``needs_review`` (hard rule 6).

Account identifiers are kept as the last four digits only. A block's name is hashed, because it can
be a user-chosen label.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from app.domain.fingerprint import import_fingerprint, normalize_description
from app.domain.money import CENT, ZERO

Sign = Literal["as_is", "invert", "outflow", "inflow"]
ReconcileStatus = Literal["reconciled", "needs_review", "balances_only"]
_SIGNS: tuple[str, ...] = ("as_is", "invert", "outflow", "inflow")


class StatementParseError(ValueError):
    """A statement could not be parsed. The message never carries statement content."""


# ----------------------------------------------------------------------------- profile model
@dataclass(frozen=True)
class RowGroup:
    """A set of transaction rows with one sign rule. ``enter`` activates it (None = always on)."""

    row: re.Pattern[str]
    sign: Sign
    enter: re.Pattern[str] | None = None
    # A row whose description wraps: ``wrap_start`` (``date``, ``desc``) on one line, then
    # ``wrap_end`` (``amount``, optional ``desc`` and ``neg``) on the next non-blank line.
    wrap_start: re.Pattern[str] | None = None
    wrap_end: re.Pattern[str] | None = None
    leave: re.Pattern[str] | None = None  # deactivates the group until the next ``enter``


@dataclass(frozen=True)
class Profile:
    id: str
    institution: str
    kind: Literal["asset", "card", "summary"]
    fingerprint: tuple[re.Pattern[str], ...]
    period: re.Pattern[str]
    period_date_format: str
    date_format: str
    balance_sign: Literal["as_is", "invert"]
    groups: tuple[RowGroup, ...] = ()
    account: re.Pattern[str] | None = None
    block_start: re.Pattern[str] | None = None
    opening: re.Pattern[str] | None = None
    closing: re.Pattern[str] | None = None
    balances: re.Pattern[str] | None = None  # summary: one regex with groups ``open`` and ``close``


_SPACED = re.compile(r"~([^~]+)~")


def _spaced(match: re.Match[str]) -> str:
    """``~Closing Date~`` -> a pattern tolerating stray spaces between letters (PDF kerning)."""
    return r"\s*".join(re.escape(c) for c in match.group(1) if not c.isspace())


def _rx(value: Any, key: str, *, flags: int = 0) -> re.Pattern[str]:
    if not isinstance(value, str):
        raise StatementParseError(f"profile key {key!r} must be a regex string")
    value = _SPACED.sub(_spaced, value)
    try:
        return re.compile(value, flags)
    except re.error as exc:
        raise StatementParseError(f"profile key {key!r} is not a valid regex") from exc


def _opt(data: Mapping[str, Any], key: str) -> re.Pattern[str] | None:
    return _rx(data[key], key, flags=re.MULTILINE) if data.get(key) else None


def profile_from_mapping(data: Mapping[str, Any]) -> Profile:
    """Validate a profile mapping (parsed YAML) into a ``Profile``."""
    try:
        kind = data["kind"]
        if kind not in ("asset", "card", "summary"):
            raise StatementParseError("profile kind must be asset, card or summary")
        balance_sign = data.get("balance_sign", "invert" if kind == "card" else "as_is")
        if balance_sign not in ("as_is", "invert"):
            raise StatementParseError("balance_sign must be as_is or invert")
        groups: list[RowGroup] = []
        for g in data.get("groups", []):
            sign = g.get("sign", "as_is")
            if sign not in _SIGNS:
                raise StatementParseError("group sign is not recognised")
            groups.append(
                RowGroup(
                    row=_rx(g["row"], "row", flags=re.MULTILINE),
                    sign=sign,
                    enter=_opt(g, "enter"),
                    wrap_start=_opt(g, "wrap_start"),
                    wrap_end=_opt(g, "wrap_end"),
                    leave=_opt(g, "leave"),
                )
            )
        fingerprint = tuple(
            _rx(p, "fingerprint", flags=re.MULTILINE | re.IGNORECASE) for p in data["fingerprint"]
        )
        if not fingerprint:
            raise StatementParseError("a profile needs at least one fingerprint regex")
        return Profile(
            id=str(data["id"]),
            institution=str(data["institution"]),
            kind=kind,
            fingerprint=fingerprint,
            period=_rx(data["period"], "period", flags=re.MULTILINE),
            period_date_format=str(data["period_date_format"]),
            date_format=str(data.get("date_format", data["period_date_format"])),
            balance_sign=balance_sign,
            groups=tuple(groups),
            account=_opt(data, "account"),
            block_start=_opt(data, "block_start"),
            opening=_opt(data, "opening"),
            closing=_opt(data, "closing"),
            balances=_opt(data, "balances"),
        )
    except KeyError as exc:
        raise StatementParseError(f"profile is missing key {exc.args[0]!r}") from exc


# ----------------------------------------------------------------------------- parsed model
@dataclass(frozen=True)
class ParsedTxn:
    posted_date: date
    description: str
    amount: Decimal  # Holdfast sign
    ordinal: int  # 1-based same-day ordinal among identical (date, amount, description) rows
    fingerprint: str


@dataclass(frozen=True)
class Reconciliation:
    status: ReconcileStatus
    reason: str | None = None
    diff: Decimal | None = None  # closing - (opening + sum), Holdfast sign


@dataclass(frozen=True)
class ParsedBlock:
    key: str  # "main", or a 10-char hash of the block name
    account_last4: str | None
    opening_balance: Decimal | None  # Holdfast sign
    closing_balance: Decimal | None  # Holdfast sign
    txns: tuple[ParsedTxn, ...]
    reconciliation: Reconciliation

    @property
    def txn_sum(self) -> Decimal:
        return sum((t.amount for t in self.txns), ZERO)


@dataclass(frozen=True)
class ParsedStatement:
    profile_id: str
    institution: str
    kind: str
    period_start: date | None
    period_end: date
    blocks: tuple[ParsedBlock, ...]

    @property
    def status(self) -> ReconcileStatus:
        if not self.blocks:
            return "needs_review"
        statuses = {b.reconciliation.status for b in self.blocks}
        if "needs_review" in statuses:
            return "needs_review"
        if statuses == {"balances_only"}:
            return "balances_only"
        return "reconciled"


# ----------------------------------------------------------------------------- primitives
_MONEY = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}")
_LONG_DIGITS = re.compile(r"\d{8,}")
MAX_LINE = 1000


def mask_long_digit_runs(text: str) -> str:
    """Replace runs of 8+ digits (reference, card or account numbers) with ``#`` (hard rule 3)."""
    return _LONG_DIGITS.sub(lambda m: "#" * len(m.group(0)), text)


def parse_amount(text: str) -> Decimal:
    """Parse a printed amount: ``$1,234.50``, ``-$4.26``, ``(12.00)``, ``5.98-``. Never a float.

    Strict: thousands groups of exactly three digits, exactly two decimals, at most one sign.
    Stray spaces (PDF kerning) are ignored; anything else raises ``StatementParseError``.
    """
    s = re.sub(r"[\s$]", "", text)
    signs = 0
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s, signs = True, s[1:-1], signs + 1
    if s.startswith(("-", "+")):
        negative, s, signs = s[0] == "-", s[1:], signs + 1
    if s.endswith("-"):
        negative, s, signs = True, s[:-1], signs + 1
    if signs > 1 or not _MONEY.fullmatch(s):
        raise StatementParseError("unreadable amount")
    value = Decimal(s.replace(",", ""))
    return -value if negative else value


def _apply_sign(printed: Decimal, sign: Sign) -> Decimal:
    if sign == "as_is":
        return printed
    if sign == "invert":
        return -printed
    if sign == "outflow":
        return -abs(printed)
    return abs(printed)


def _parse_date(text: str, fmt: str, period_end: date, period_start: date | None = None) -> date:
    """Read a printed row date. A year-less date takes the year that best fits the statement.

    Candidates are the period end's year and its neighbours (Dec/Jan statements, a row a few days
    past the end). Prefer a candidate inside the period, then one in the posting grace, then
    any candidate. Within each group the candidate nearest the period end wins. Card statements
    with no printed start allow two months back. A candidate year where the day does not exist
    (Feb 29) is skipped. Dates that print a year are taken as printed.
    """
    if "%y" in fmt or "%Y" in fmt:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            raise StatementParseError("unreadable date") from None
    try:
        # 2000 is a leap year, so Feb 29 parses; the real year is chosen below.
        month_day = datetime.strptime(f"{text} 2000", f"{fmt} %Y").date()
    except ValueError:
        raise StatementParseError("unreadable date") from None
    candidates: list[date] = []
    for year in (period_end.year - 1, period_end.year, period_end.year + 1):
        try:
            candidates.append(date(year, month_day.month, month_day.day))
        except ValueError:
            continue  # no Feb 29 in that year
    if not candidates:
        raise StatementParseError("unreadable date")
    lower = period_start if period_start is not None else _months_back(period_end, 2)
    upper = period_end + timedelta(days=7)
    inside = [d for d in candidates if lower <= d <= period_end]
    with_grace = [d for d in candidates if lower <= d <= upper]
    return min(inside or with_grace or candidates, key=lambda d: abs((d - period_end).days))


def _block_key(name: str) -> str:
    return hashlib.sha256(" ".join(name.split()).casefold().encode()).hexdigest()[:10]


def detect_profile(text: str, profiles: Sequence[Profile]) -> Profile | None:
    """The profile whose fingerprints all match; the one with most fingerprints wins a tie-break.

    Returns ``None`` when nothing matches or when two equally specific profiles both match.
    """
    hits = [p for p in profiles if all(f.search(text) for f in p.fingerprint)]
    if not hits:
        return None
    top = max(len(p.fingerprint) for p in hits)
    best = [p for p in hits if len(p.fingerprint) == top]
    return best[0] if len(best) == 1 else None


# ----------------------------------------------------------------------------- parsing
@dataclass
class _Block:
    key: str
    last4: str | None
    opening: Decimal | None = None
    closing: Decimal | None = None
    rows: list[tuple[date, str, Decimal]] = field(default_factory=list)


def _balance(pattern: re.Pattern[str] | None, line: str) -> Decimal | None:
    if pattern is None:
        return None
    m = pattern.search(line)
    if not m:
        return None
    return parse_amount(m.group("v"))


def _hbal(value: Decimal | None, profile: Profile) -> Decimal | None:
    if value is None:
        return None
    return -value if profile.balance_sign == "invert" else value


def reconcile(
    opening: Decimal | None, closing: Decimal | None, amounts: Sequence[Decimal]
) -> Reconciliation:
    """opening + sum(amounts) = closing within one cent, else ``needs_review``."""
    if opening is None or closing is None:
        return Reconciliation("needs_review", "missing_balance")
    diff = closing - (opening + sum(amounts, ZERO))
    if abs(diff) <= CENT:
        return Reconciliation("reconciled", None, diff)
    return Reconciliation("needs_review", "balance_mismatch", diff)


def parse_statement(text: str, profile: Profile) -> ParsedStatement:
    """Parse layout ``text`` with ``profile``. Raises ``StatementParseError`` without content."""
    pm = profile.period.search(text)
    if not pm:
        raise StatementParseError("period_not_found")
    try:
        end = datetime.strptime("".join(pm.group("end").split()), profile.period_date_format).date()
        start = (
            datetime.strptime("".join(pm.group("start").split()), profile.period_date_format).date()
            if "start" in pm.groupdict()
            else None
        )
    except ValueError:
        raise StatementParseError("unreadable period") from None

    stmt_last4: str | None = None
    if profile.account:
        am = profile.account.search(text)
        if am is None:
            raise StatementParseError("account_not_found")
        stmt_last4 = am.group("last4")

    if profile.kind == "summary":
        return _parse_summary(text, profile, start, end, stmt_last4)

    blocks: dict[str, _Block] = {"main": _Block("main", stmt_last4)}
    order = ["main"]
    cur = blocks["main"]
    pending: tuple[str, str] | None = None
    pending_date: str | None = None
    pending_desc: str | None = None
    active: RowGroup | None = next((g for g in profile.groups if g.enter is None), None)

    for line in text.splitlines():
        if len(line) > MAX_LINE:  # not a ledger line; also bounds regex work per line
            continue
        if profile.block_start and (bm := profile.block_start.search(line)):
            name = bm.groupdict().get("name") or bm.group(0)
            last4 = bm.groupdict().get("last4") or stmt_last4
            key = _block_key(f"{name}|{last4 or ''}")
            if key not in blocks:
                blocks[key] = _Block(key, last4)
                order.append(key)
            cur = blocks[key]
            continue
        for g in profile.groups:
            if g.enter and g.enter.search(line):
                active = g
                break
        if cur.opening is None:
            cur.opening = _balance(profile.opening, line)
        if cur.closing is None:
            cur.closing = _balance(profile.closing, line)
        if active is None:
            continue
        if active.leave and active.leave.search(line):
            active = None
            pending = None
            continue
        rm = active.row.search(line)
        if rm is None and pending is not None and active.wrap_end:
            em = active.wrap_end.search(line)
            if em is not None and line.strip():
                rm = em
                pending_date, pending_desc = pending
            else:
                pending_date = pending_desc = None
        else:
            pending_date = pending_desc = None
        if rm is not None:
            groups_ = rm.groupdict()
            desc = " ".join(((pending_desc or "") + " " + (groups_.get("desc") or "")).split())
            if groups_.get("date"):
                row_date = _parse_date(rm.group("date"), profile.date_format, end, start)
            elif pending_date is not None:
                row_date = _parse_date(pending_date, profile.date_format, end, start)
            else:
                row_date = end
            printed = parse_amount(rm.group("amount") + ("-" if groups_.get("neg") else ""))
            amount = _apply_sign(printed, active.sign)
            if amount != ZERO:  # a $0.00 row (e.g. zero interest) changes nothing; not imported
                cur.rows.append((row_date, desc, amount))
            pending = None
        elif line.strip():
            sm = active.wrap_start.search(line) if active.wrap_start else None
            pending = (sm.group("date"), sm.group("desc")) if sm else None

    parsed: list[ParsedBlock] = []
    for key in order:
        b = blocks[key]
        if (
            key == "main"
            and len(order) > 1
            and b.rows == []
            and b.opening is None
            and b.closing is None
        ):
            continue
        parsed.append(_finish_block(b, profile, start, end))
    return ParsedStatement(profile.id, profile.institution, profile.kind, start, end, tuple(parsed))


def _months_back(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 - months, 12)
    return date(y, m + 1, min(d.day, 28))


def _finish_block(b: _Block, profile: Profile, start: date | None, end: date) -> ParsedBlock:
    seen: dict[tuple[date, Decimal, str], int] = {}
    txns: list[ParsedTxn] = []
    for posted, desc, amount in b.rows:
        k = (posted, amount, normalize_description(desc))
        seen[k] = seen.get(k, 0) + 1
        txns.append(
            ParsedTxn(
                posted, desc, amount, seen[k], import_fingerprint(posted, amount, desc, seen[k])
            )
        )
    opening, closing = _hbal(b.opening, profile), _hbal(b.closing, profile)
    rec = reconcile(opening, closing, [t.amount for t in txns])
    # A row dated well outside the statement period means a misread date or the wrong document.
    # A card statement has no printed start, so allow its whole cycle plus posting lag.
    lower = _months_back(start, 1) if start else _months_back(end, 2)
    upper = end + timedelta(days=7)
    if rec.status == "reconciled" and any(not lower <= t.posted_date <= upper for t in txns):
        rec = Reconciliation("needs_review", "date_out_of_period", rec.diff)
    return ParsedBlock(b.key, b.last4, opening, closing, tuple(txns), rec)


def _parse_summary(
    text: str, profile: Profile, start: date | None, end: date, last4: str | None
) -> ParsedStatement:
    if profile.balances is None or not (m := profile.balances.search(text)):
        raise StatementParseError("balances_not_found")
    block = ParsedBlock(
        "main",
        last4,
        _hbal(parse_amount(m.group("open")), profile),
        _hbal(parse_amount(m.group("close")), profile),
        (),
        Reconciliation("balances_only", "no_transactions_in_document"),
    )
    return ParsedStatement(profile.id, profile.institution, profile.kind, start, end, (block,))
