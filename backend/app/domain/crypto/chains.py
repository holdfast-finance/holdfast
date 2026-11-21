"""The chain registry, as data (ADR 0020). Adding a chain is adding a row, not code.

Chain ids are the public EIP-155 ids. Nothing here names an endpoint: node URLs come only from the
household's own configuration, and there is no default public RPC or indexer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.domain.crypto.errors import CryptoInputError


class ChainKind(StrEnum):
    BITCOIN = "bitcoin"
    EVM = "evm"


@dataclass(frozen=True)
class Chain:
    slug: str  # stored in crypto_wallet.chain and crypto_tx.chain
    name: str
    kind: ChainKind
    native_symbol: str
    native_decimals: int
    chain_id: int | None = None  # EIP-155, EVM only


CHAINS: tuple[Chain, ...] = (
    Chain("bitcoin", "Bitcoin", ChainKind.BITCOIN, "BTC", 8),
    Chain("ethereum", "Ethereum", ChainKind.EVM, "ETH", 18, chain_id=1),
    Chain("base", "Base", ChainKind.EVM, "ETH", 18, chain_id=8453),
    Chain("arbitrum", "Arbitrum One", ChainKind.EVM, "ETH", 18, chain_id=42161),
    Chain("optimism", "OP Mainnet", ChainKind.EVM, "ETH", 18, chain_id=10),
    Chain("polygon", "Polygon PoS", ChainKind.EVM, "POL", 18, chain_id=137),
)
_BY_SLUG = {c.slug: c for c in CHAINS}
EVM_CHAINS: tuple[Chain, ...] = tuple(c for c in CHAINS if c.kind is ChainKind.EVM)


def get_chain(slug: str) -> Chain:
    try:
        return _BY_SLUG[slug]
    except KeyError:
        raise CryptoInputError("That chain is not supported.") from None
