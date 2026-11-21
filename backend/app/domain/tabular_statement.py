"""Shared results and normalization for CSV and OFX transaction exports."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.fingerprint import import_fingerprint, normalize_description
from app.domain.money import ZERO, quantize_cents
from app.domain.statement_parse import ParsedTxn, Reconciliation, mask_long_digit_runs, reconcile


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

    def add(
        self,
        posted: date,
        description: str,
        amount: Decimal,
        balance: Decimal | None,
        external_id: str | None = None,
    ) -> None:
        description = mask_long_digit_runs(" ".join(description.split()))
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

    def reconcile(
        self,
        closing: Decimal | None,
        *,
        newest_first: bool = False,
    ) -> tuple[Decimal | None, Reconciliation]:
        """Compare chronological post-transaction balances and the statement closing balance."""
        opening = None
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
                return opening, check
        return opening, reconcile(opening, closing, [running_total])
