"""Provider-neutral values exchanged between connectors and ingest."""

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import date
from decimal import Decimal
from typing import Literal

# These values match the ledger schema and account constraint.
AccountType = Literal["depository", "credit", "loan", "investment", "other"]


_CENTS = Decimal("0.01")
# NUMERIC(14,2) holds |value| < 10**12; connectors are held to a tighter 10**10 so a bad
# provider value can never reach the column limit.
_MONEY_LIMIT = Decimal("10000000000")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


def _require_cents(value: object, field: str) -> Decimal:
    """Validate a money value and return it in canonical two-place form."""
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be a Decimal")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError(f"{field} must be finite")
    if exponent < -2:
        raise ValueError(f"{field} must have at most two decimal places")
    if abs(value) >= _MONEY_LIMIT:
        raise ValueError(f"{field} is out of range")
    canonical = value.quantize(_CENTS)
    return canonical + Decimal("0.00") if canonical.is_zero() else canonical


def _set(obj: object, field: str, value: object) -> None:
    object.__setattr__(obj, field, value)  # frozen dataclass: canonicalise once at construction


@dataclass(frozen=True)
class ConnectorAccount:
    """An account as reported by a provider.

    ``currency`` is an ISO 4217 code (three upper-case letters). ``None`` means the provider did
    not say: ingest must refuse or flag the account, and must never assume USD.
    """

    external_account_id: str
    name: str = dc_field(repr=False)
    official_name: str | None = dc_field(repr=False)
    mask: str | None
    type: AccountType
    subtype: str | None
    currency: str | None
    institution_name: str | None

    def __post_init__(self) -> None:
        if self.currency is not None and not _CURRENCY.fullmatch(self.currency):
            raise ValueError("currency must be three upper-case letters or None")


@dataclass(frozen=True)
class ConnectorBalance:
    """Balances in Holdfast's sign convention.

    ``current`` is positive for assets and negative when the balance is owed (credit and loan).
    ``limit`` is never negative. ``available`` is non-negative, except on a depository account in
    overdraft. For a credit account, ``available == limit + current``.
    """

    external_account_id: str
    as_of: date
    current: Decimal | None
    available: Decimal | None
    limit: Decimal | None

    def __post_init__(self) -> None:
        for field, value in (
            ("current", self.current),
            ("available", self.available),
            ("limit", self.limit),
        ):
            if value is not None:
                _set(self, field, _require_cents(value, field))
        if self.limit is not None and self.limit < 0:
            raise ValueError("limit must not be negative")


@dataclass(frozen=True)
class ConnectorTxn:
    """A transaction in Holdfast's sign convention (outflows are negative).

    A pending row's ``posted_date`` is its pending date. A pending amount change arrives in
    ``SyncBatch.modified`` with the same ``external_id``. A posted row that replaces a pending one
    names it in ``pending_external_id``, which is never its own ``external_id``.

    Investment buys and sells must not use ``amount``: they get their own types later.
    """

    external_account_id: str
    external_id: str
    posted_date: date
    authorized_date: date | None
    amount: Decimal
    description_raw: str = dc_field(repr=False)
    merchant_name: str | None
    pfc_primary: str | None
    pfc_detailed: str | None
    is_pending: bool
    pending_external_id: str | None

    def __post_init__(self) -> None:
        _set(self, "amount", _require_cents(self.amount, "amount"))
        if not self.external_id:
            raise ValueError("external_id must not be empty")
        if self.pending_external_id is not None and (
            not self.pending_external_id or self.pending_external_id == self.external_id
        ):
            raise ValueError("pending_external_id must be non-empty and differ from external_id")
        if self.is_pending and self.pending_external_id is not None:
            raise ValueError("a pending transaction cannot have a pending_external_id")


@dataclass(frozen=True)
class SyncBatch:
    """One fetch result. Removal keys pair external account and transaction IDs.

    Apply order: ``added`` first (a row naming ``pending_external_id`` posts that pending row),
    then ``modified``, then ``removed``. A removal of an id named by an added row's
    ``pending_external_id`` is the pending row being posted, not a deletion.

    ``next_cursor`` is always the resume cursor: persist it after the batch commits, even when
    ``has_more`` is false. ``has_more`` says whether to fetch again straight away.
    """

    accounts: tuple[ConnectorAccount, ...]
    balances: tuple[ConnectorBalance, ...]
    added: tuple[ConnectorTxn, ...]
    modified: tuple[ConnectorTxn, ...]
    removed: tuple[tuple[str, str], ...]
    next_cursor: str | None
    has_more: bool
    history_complete: bool

    def __post_init__(self) -> None:
        if self.has_more and self.next_cursor is None:
            raise ValueError("has_more requires a next_cursor")
