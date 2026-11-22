"""Provider-neutral values exchanged between connectors and ingest."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

# These values match the ledger schema and account constraint.
AccountType = Literal["depository", "credit", "loan", "investment", "other"]


def _require_cents(value: object, field: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field} must be a Decimal")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError(f"{field} must be finite")
    if exponent < -2:
        raise ValueError(f"{field} must have at most two decimal places")


@dataclass(frozen=True)
class ConnectorAccount:
    external_account_id: str
    name: str
    official_name: str | None
    mask: str | None
    type: AccountType
    subtype: str | None
    currency: str | None
    institution_name: str | None


@dataclass(frozen=True)
class ConnectorBalance:
    """Current is positive for assets and negative for amounts owed."""

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
                _require_cents(value, field)


@dataclass(frozen=True)
class ConnectorTxn:
    """A transaction in Holdfast's sign convention (outflows are negative)."""

    external_account_id: str
    external_id: str
    posted_date: date
    authorized_date: date | None
    amount: Decimal
    description_raw: str
    merchant_name: str | None
    pfc_primary: str | None
    pfc_detailed: str | None
    is_pending: bool
    pending_external_id: str | None

    def __post_init__(self) -> None:
        _require_cents(self.amount, "amount")
        if self.is_pending and self.pending_external_id is not None:
            raise ValueError("a pending transaction cannot have a pending_external_id")


@dataclass(frozen=True)
class SyncBatch:
    """One fetch result. Removal keys pair external account and transaction IDs."""

    accounts: tuple[ConnectorAccount, ...]
    balances: tuple[ConnectorBalance, ...]
    added: tuple[ConnectorTxn, ...]
    modified: tuple[ConnectorTxn, ...]
    removed: tuple[tuple[str, str], ...]
    next_cursor: str | None
    history_complete: bool
