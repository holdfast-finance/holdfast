"""Shared results and normalization for CSV and OFX transaction exports."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.fingerprint import import_fingerprint, normalize_description
from app.domain.money import ZERO, quantize_cents
from app.domain.statement_parse import ParsedTxn, Reconciliation, mask_long_digit_runs, reconcile

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
_DIGIT_RUN = re.compile(r"[0-9]{8,}")


def resolve_encoding(name: str) -> str | None:
    """The canonical codec for an allowlisted encoding name, else ``None``."""
    return _ENCODINGS.get(name.strip().lower())


def mask_identifiers(text: str) -> str:
    """Mask long digit runs and separated card numbers with ``#`` (hard rule 3)."""
    text = _SEPARATED_CARD.sub(lambda m: re.sub(r"[0-9]", "#", m[0]), text)
    return mask_long_digit_runs(text)


def mask_external_id(value: str) -> str:
    """Replace each digit run of 8+ with ``#`` and a short hash of the run.

    Deterministic, so the id still works as a dedupe key, but the digits themselves are gone.
    """
    return _DIGIT_RUN.sub(lambda m: "#" + hashlib.sha256(m[0].encode()).hexdigest()[:8], value)


def _zero_clean(value: Decimal) -> Decimal:
    """``-0.00`` is just zero: one spelling, so fingerprints agree across sources and signs."""
    return Decimal("0.00") if value == 0 else value


@dataclass(frozen=True)
class ExportTxn(ParsedTxn):
    external_id: str | None = None


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
    ) -> None:
        amount = _zero_clean(amount)
        balance = None if balance is None else _zero_clean(balance)
        description = mask_identifiers(" ".join(description.split()))
        if external_id is not None:
            external_id = mask_external_id(external_id)
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
            )
        )
        self.balances.append(balance)

    def _flagged(self, result: Reconciliation) -> Reconciliation:
        if not self._flags:
            return result
        return Reconciliation("needs_review", ",".join(self._flags), result.diff)

    def reconcile(
        self,
        closing: Decimal | None,
        *,
        newest_first: bool = False,
        opening: Decimal | None = None,
        mismatch_reason: str = "balance_mismatch",
    ) -> tuple[Decimal | None, Reconciliation]:
        """Compare chronological post-transaction balances and the statement closing balance.

        ``opening`` is a known opening balance (from the prior statement). Without one, the
        opening is derived from the first running balance, and a file with neither stays
        ``needs_review`` / ``missing_balance``.
        """
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
