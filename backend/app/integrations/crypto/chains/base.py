"""The ``ChainSource`` port and its value types (ADR 0020).

A source answers two questions for a set of **public** addresses: what do they hold, and what
moved. Quantities are signed ``Decimal`` (inflow positive, outflow negative, hard rule 1) and
exact: adapters convert from base units with :func:`app.domain.crypto.units.from_base_units`, never
through a float.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable


class ChainSourceError(Exception):
    """The source failed. The message never carries an endpoint URL, key or address list."""


class HistoryUnavailable(ChainSourceError):
    """The endpoint cannot list history for this kind of movement (for example no ``trace_filter``).

    The service reports it plainly instead of guessing, and the wallet is not marked reconciled.
    """


@dataclass(frozen=True)
class AddressBalance:
    address: str
    asset: str
    asset_contract: str  # '' for the chain's native asset
    quantity: Decimal  # confirmed balance, never negative


@dataclass(frozen=True)
class ChainTransfer:
    """One asset movement of one watched address, the unit the service upserts as ``crypto_tx``."""

    tx_hash: str  # lower-case; EVM with 0x
    address: str  # the watched address this movement belongs to
    direction: Literal["in", "out"]
    asset: str
    asset_contract: str
    quantity: Decimal  # signed: in >= 0, out <= 0
    event_index: int  # output / input / log / trace position inside the transaction
    block_height: int | None  # None while unconfirmed
    block_time: datetime | None
    counterparty: str | None = None
    fee_asset: str | None = None
    fee_qty: Decimal | None = None  # non-negative, only on the movement that paid the fee


@runtime_checkable
class ChainSource(Protocol):
    """Balances and transaction history for a set of addresses on one chain."""

    chain: str

    async def balances(self, addresses: Sequence[str]) -> list[AddressBalance]: ...

    async def history(
        self, addresses: Sequence[str], *, since_height: int | None = None
    ) -> list[ChainTransfer]: ...
