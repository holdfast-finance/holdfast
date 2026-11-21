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
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.domain.fingerprint import import_fingerprint, normalize_description
from app.domain.money import CENT, ZERO

Sign = Literal["as_is", "invert", "outflow", "inflow"]
ReconcileStatus = Literal["reconciled", "needs_review", "balances_only"]
_SIGNS: tuple[str, ...] = ("as_is", "invert", "outflow", "inflow")
_AMOUNT_CLEAN = re.compile(r"[\s$,+]")


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
def parse_amount(text: str) -> Decimal:
    """Parse a printed amount: ``$1,234.50``, ``-$4.26``, ``(12.00)``, ``5.98-``. Never a float."""
    s = _AMOUNT_CLEAN.sub("", text)
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.startswith("-"):
        negative, s = not negative, s[1:]
    if s.endswith("-"):
        negative, s = not negative, s[:-1]
    if not re.fullmatch(r"\d+(\.\d+)?", s):
        raise StatementParseError("unreadable amount")
    try:
        value = Decimal(s)
    except InvalidOperation as exc:  # pragma: no cover - fullmatch guards this
        raise StatementParseError("unreadable amount") from exc
    return -value if negative else value


def _apply_sign(printed: Decimal, sign: Sign) -> Decimal:
    if sign == "as_is":
        return printed
    if sign == "invert":
        return -printed
    if sign == "outflow":
        return -abs(printed)
    return abs(printed)


def _parse_date(text: str, fmt: str, period_end: date) -> date:
    has_year = "%y" in fmt or "%Y" in fmt
    try:
        if has_year:
            return datetime.strptime(text, fmt).date()
        d = datetime.strptime(f"{text} {period_end.year}", f"{fmt} %Y").date()
    except ValueError as exc:
        raise StatementParseError("unreadable date") from exc
    # A year-less row dated after the period end belongs to the previous year (Dec/Jan statement).
    return d.replace(year=d.year - 1) if d > period_end and d.month > period_end.month else d


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
    except ValueError as exc:
        raise StatementParseError("unreadable period") from exc

    stmt_last4: str | None = None
    if profile.account and (am := profile.account.search(text)):
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
                row_date = _parse_date(rm.group("date"), profile.date_format, end)
            elif pending_date is not None:
                row_date = _parse_date(pending_date, profile.date_format, end)
            else:
                row_date = end
            printed = parse_amount(rm.group("amount") + ("-" if groups_.get("neg") else ""))
            cur.rows.append((row_date, desc, _apply_sign(printed, active.sign)))
            pending = None
        elif line.strip():
            sm = active.wrap_start.search(line) if active.wrap_start else None
            pending = (sm.group("date"), sm.group("desc")) if sm else None

    parsed: list[ParsedBlock] = []
    for key in order:
        b = blocks[key]
        if key == "main" and len(order) > 1 and not (b.rows or b.opening or b.closing):
            continue
        parsed.append(_finish_block(b, profile))
    return ParsedStatement(profile.id, profile.institution, profile.kind, start, end, tuple(parsed))


def _finish_block(b: _Block, profile: Profile) -> ParsedBlock:
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
