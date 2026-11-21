"""An in-memory ``ChainSource`` used by every test (fixture-first).

Holds a ledger of ``ChainTransfer`` rows and derives balances from them, the way a consistent chain
would. ``extra_balance`` models value that arrives without a transaction (a staking withdrawal),
which is what makes a wallet fail reconciliation.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from app.integrations.crypto.chains.base import (
    AddressBalance,
    ChainSourceError,
    ChainTransfer,
    HistoryUnavailable,
)


class FakeChainSource:
    def __init__(
        self,
        chain: str,
        transfers: Sequence[ChainTransfer] = (),
        *,
        extra_balance: dict[tuple[str, str, str], Decimal] | None = None,
        history_unavailable: bool = False,
        fail: bool = False,
    ) -> None:
        self.chain = chain
        self.transfers = list(transfers)
        self.extra_balance = dict(extra_balance or {})
        self.history_unavailable = history_unavailable
        self.fail = fail
        self.history_calls: list[tuple[tuple[str, ...], int | None]] = []

    async def balances(self, addresses: Sequence[str]) -> list[AddressBalance]:
        if self.fail:
            raise ChainSourceError("chain source is unavailable")
        wanted = set(addresses)
        totals: dict[tuple[str, str, str], Decimal] = {}
        for t in self.transfers:
            if t.address in wanted:
                key = (t.address, t.asset, t.asset_contract)
                fee = t.fee_qty if t.fee_qty is not None and t.fee_asset == t.asset else 0
                totals[key] = totals.get(key, Decimal(0)) + t.quantity - Decimal(fee)
        for key, extra in self.extra_balance.items():
            if key[0] in wanted:
                totals[key] = totals.get(key, Decimal(0)) + extra
        return [
            AddressBalance(address, asset, contract, quantity)
            for (address, asset, contract), quantity in sorted(totals.items())
        ]

    async def history(
        self, addresses: Sequence[str], *, since_height: int | None = None
    ) -> list[ChainTransfer]:
        self.history_calls.append((tuple(addresses), since_height))
        if self.fail:
            raise ChainSourceError("chain source is unavailable")
        if self.history_unavailable:
            raise HistoryUnavailable("this endpoint cannot list transaction history")
        wanted = set(addresses)
        return [
            t
            for t in self.transfers
            if t.address in wanted
            and (since_height is None or t.block_height is None or t.block_height >= since_height)
        ]
