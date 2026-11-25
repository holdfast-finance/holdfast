"""Shared results and normalization for CSV and OFX transaction exports."""

import hashlib
import hmac
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.fingerprint import import_fingerprint, normalize_description
from app.domain.money import ZERO, quantize_cents
from app.domain.statement_parse import (
    ParsedTxn,
    Reconciliation,
    StatementParseError,
    mask_long_digit_runs,
    reconcile,
)

# Text encodings an export may declare. Anything else (utf-7, codecs with side effects, unknown
# names) is refused: an attacker-chosen codec must never decide how a file is decoded.
_ENCODINGS = {
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "utf-8-sig": "utf-8-sig",
    "utf-16": "utf-16",
    "utf-16le": "utf-16-le",
    "utf-16-le": "utf-16-le",
    "utf-16be": "utf-16-be",
    "utf-16-be": "utf-16-be",
    "utf-32": "utf-32",
    "utf-32le": "utf-32-le",
    "utf-32-le": "utf-32-le",
    "utf-32be": "utf-32-be",
    "utf-32-be": "utf-32-be",
    "cp1252": "cp1252",
    "windows-1252": "cp1252",
    "1252": "cp1252",
    "iso-8859-1": "iso-8859-1",
    "iso8859-1": "iso-8859-1",
    "8859-1": "iso-8859-1",
    "latin-1": "iso-8859-1",
    "latin1": "iso-8859-1",
    "ascii": "ascii",
    "us-ascii": "ascii",
    "usascii": "ascii",
}

# Card numbers printed with separators, 4-4-4-4 or the 4-6-5 American Express layout. Fixed-width
# groups, so matching is linear. (The shared PDF helper only masks unbroken runs: changing it
# would change existing fingerprints, so the stricter rule lives in this path only.)
_SEPARATED_CARD = re.compile(
    r"(?<![0-9])(?:[0-9]{4}(?:[ -][0-9]{4}){3}|[0-9]{4}[ -][0-9]{6}[ -][0-9]{5})(?![0-9])"
)
MIN_ID_KEY_BYTES = 16
_MAX_INTEGER_DIGITS = 12  # NUMERIC(14,2)
# Control characters (C0, DEL, C1) never belong in a description: they become spaces.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def resolve_encoding(name: str) -> str | None:
    """The canonical codec for an allowlisted encoding name, else ``None``."""
    return _ENCODINGS.get(name.strip().lower())


def mask_identifiers(text: str) -> str:
    """Mask long digit runs and separated card numbers with ``#`` (hard rule 3)."""
    text = _SEPARATED_CARD.sub(lambda m: re.sub(r"[0-9]", "#", m[0]), text)
    return mask_long_digit_runs(text)


def check_id_key(id_key: bytes) -> None:
    """Reject a missing or weak id key (a programming error, so ``ValueError``)."""
    if not isinstance(id_key, bytes) or len(id_key) < MIN_ID_KEY_BYTES:
        raise ValueError(f"id_key must be at least {MIN_ID_KEY_BYTES} bytes")


def external_id_token(value: str, id_key: bytes) -> str:
    """Replace a source-supplied id with HMAC-SHA256(key, id), 128 bits as 32 hex characters.

    A FITID can embed a card or account number, so no character of the original is kept and an
    unkeyed hash (which a short digit string could be brute-forced from) is not enough. The token
    is deterministic per key, so it still works as a dedupe key. The domain never reads settings
    or the environment: the caller supplies the key. Deriving a per-household key (so tokens do
    not correlate across households) belongs to the persistence layer.
    """
    check_id_key(id_key)
    return hmac.new(id_key, value.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()[:32]


def check_amount_bounds(value: Decimal) -> Decimal:
    """At most 12 integer digits, like MoneyStr and NUMERIC(14,2)."""
    if not value.is_finite() or abs(value) >= Decimal(10) ** _MAX_INTEGER_DIGITS:
        raise StatementParseError("amount out of range")
    return value


def check_opening_balance(value: Decimal | None) -> Decimal | None:
    """An opening balance: ``Decimal``, at most 2 decimals and 12 integer digits."""
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise StatementParseError("opening balance must be a Decimal")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise StatementParseError("opening balance allows at most 2 decimal places")
    if abs(value) >= Decimal(10) ** _MAX_INTEGER_DIGITS:
        raise StatementParseError("opening balance allows at most 12 integer digits")
    return value


def _zero_clean(value: Decimal) -> Decimal:
    """``-0.00`` is just zero: one spelling, so fingerprints agree across sources and signs."""
    return Decimal("0.00") if value == 0 else value


@dataclass(frozen=True)
class ExportTxn(ParsedTxn):
    external_id: str | None = None  # keyed token, never the source id
    correction: str | None = None  # "replace": this row replaces an earlier one


@dataclass(frozen=True)
class TransactionExport:
    txns: tuple[ExportTxn, ...]
    reconciliation: Reconciliation
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None


class ExportRows:
    def __init__(self) -> None:
        self.txns: list[ExportTxn] = []
        self.balances: list[Decimal | None] = []
        self._ordinals: Counter[tuple[date, str, str]] = Counter()
        self._flags: list[str] = []

    def flag(self, reason: str) -> None:
        """Force needs_review with a named reason (kept in first-seen order, once)."""
        if reason not in self._flags:
            self._flags.append(reason)

    def add(
        self,
        posted: date,
        description: str,
        amount: Decimal,
        balance: Decimal | None,
        external_id: str | None = None,
        correction: str | None = None,
    ) -> None:
        amount = _zero_clean(amount)
        balance = None if balance is None else _zero_clean(balance)
        description = mask_identifiers(" ".join(_CONTROL_CHARS.sub(" ", description).split()))
        key = (posted, str(quantize_cents(amount)), normalize_description(description))
        self._ordinals[key] += 1
        ordinal = self._ordinals[key]
        self.txns.append(
            ExportTxn(
                posted_date=posted,
                description=description,
                amount=amount,
                ordinal=ordinal,
                fingerprint=import_fingerprint(posted, amount, description, ordinal),
                external_id=external_id,
                correction=correction,
            )
        )
        self.balances.append(balance)

    def _flagged(self, result: Reconciliation) -> Reconciliation:
        if not self._flags:
            return result
        # Every applicable reason, flags first: a flag must never hide a balance mismatch.
        reasons = list(self._flags)
        if result.reason and result.reason not in reasons:
            reasons.append(result.reason)
        return Reconciliation("needs_review", ",".join(reasons), result.diff)

    def reconcile(
        self,
        closing: Decimal | None,
        *,
        newest_first: bool = False,
        opening: Decimal | None = None,
        mismatch_reason: str = "balance_mismatch",
    ) -> tuple[Decimal | None, Reconciliation]:
        """Compare chronological post-transaction balances and the statement closing balance.

        ``opening`` is a known opening balance (from the prior statement), a ``Decimal`` in
        Holdfast's sign: a card balance owed is negative. Without one, the opening is derived
        from the first running balance, and a file with neither stays ``needs_review`` /
        ``missing_balance``.
        """
        opening = check_opening_balance(opening)
        running_total = ZERO
        indices = range(len(self.txns))
        order = reversed(indices) if newest_first else iter(indices)
        for position, index in enumerate(order):
            running_total += self.txns[index].amount
            balance = self.balances[index]
            if balance is None:
                continue
            if opening is None and position < len(self.txns) - 1:
                # A balance at closing cannot independently establish an opening balance.
                opening = balance - running_total
            check = reconcile(opening, balance, [running_total])
            if check.reason == "balance_mismatch":
                return opening, self._flagged(check)
        final = reconcile(opening, closing, [running_total])
        if final.reason == "balance_mismatch":
            final = Reconciliation(final.status, mismatch_reason, final.diff)
        return opening, self._flagged(final)
