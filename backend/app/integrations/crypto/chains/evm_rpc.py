"""EVM chains through a JSON-RPC endpoint you run (ADR 0020).

- Native balance: ``eth_getBalance``. Token balances: ``eth_call balanceOf`` for contracts seen in
  history.
- ERC-20 history: ``eth_getLogs`` on the indexed ``Transfer`` topic for the watched addresses
  (ERC-721, which shares the topic with four topics, is ignored).
- Native history: ``trace_filter`` (Erigon, Reth, Nethermind). A node without it cannot list native
  movements by address, so the source raises :class:`HistoryUnavailable` instead of guessing. Gas
  is taken from the receipt (``gasUsed x effectiveGasPrice``, plus ``l1Fee`` on OP-stack chains)
  and attached to the movement the watched sender made, so ``sum(quantity) - sum(fee)`` equals the
  balance. Value that arrives without a transaction (a staking withdrawal) is not a trace, so such
  a wallet will not reconcile and is flagged for review.

All quantities come from integers through :func:`from_base_units`; nothing passes through a float.
The endpoint URL is never put in an error message.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.domain.crypto.chains import ChainKind, get_chain
from app.domain.crypto.errors import CryptoInputError
from app.domain.crypto.evm import keccak256, normalise_evm_address
from app.domain.crypto.units import from_base_units
from app.integrations.crypto.chains.base import (
    AddressBalance,
    ChainSourceError,
    ChainTransfer,
    HistoryUnavailable,
)

TRANSFER_TOPIC = "0x" + keccak256(b"Transfer(address,address,uint256)").hex()
SELECTOR_DECIMALS = "0x313ce567"
SELECTOR_SYMBOL = "0x95d89b41"
SELECTOR_BALANCE_OF = "0x70a08231"
METHOD_NOT_FOUND = -32601
TIMEOUT_SECONDS = 30


class MethodNotFoundError(ChainSourceError):
    pass


class JsonRpc(Protocol):
    async def call(self, method: str, params: list[Any]) -> Any: ...


class HttpJsonRpc:
    def __init__(self, url: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = url
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        self._id = 0

    async def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        try:
            response = await self._client.post(self._url, json=body)
            response.raise_for_status()
            reply = response.json()
        except (httpx.HTTPError, ValueError):
            # httpx error text includes the URL, which can hold an API key.
            raise ChainSourceError("the EVM endpoint did not answer") from None
        error = reply.get("error")
        if error:
            if error.get("code") == METHOD_NOT_FOUND:
                raise MethodNotFoundError("the EVM endpoint does not support this method")
            raise ChainSourceError("the EVM endpoint refused the request")
        return reply.get("result")


def _int(value: str | int | None) -> int:
    if value is None:
        return 0
    return value if isinstance(value, int) else int(value, 16)


def _pad(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def _topic_address(topic: str) -> str:
    return normalise_evm_address("0x" + topic[-40:])


def trace_event_index(path: tuple[int, ...]) -> int:
    """A stable position for a trace inside its transaction, from its own ``traceAddress``.

    Not an enumeration of whatever the node returned: that would shift when the watched addresses
    change (a different filter returns different traces) and a re-sync would duplicate rows. The top
    level call is 0, its first child 1, and so on, base 1000 per level, folded to fit ``integer``.
    """
    index = 0
    for step in path:
        index = index * 1000 + min(step, 998) + 1
    return index % 2_000_000_000


def decode_abi_string(data: str) -> str:
    """Decode an ABI ``string`` return, or the legacy ``bytes32`` some old tokens use."""
    raw = bytes.fromhex(data[2:])
    if len(raw) == 32:
        return raw.rstrip(b"\x00").decode("utf-8", "replace")
    if len(raw) >= 64:
        offset = int.from_bytes(raw[:32], "big")
        length = int.from_bytes(raw[offset : offset + 32], "big")
        return raw[offset + 32 : offset + 32 + length].decode("utf-8", "replace")
    return ""


def clean_symbol(symbol: str) -> str:
    """A symbol the schema accepts. Token symbols are chosen by the token's author and can lie
    (anyone can deploy "USDC"), so the contract address, not this, identifies the asset."""
    cleaned = "".join(c for c in symbol.upper() if c.isascii() and (c.isalnum() or c in "._-"))
    cleaned = cleaned.lstrip("._-")[:32]
    return cleaned or "UNKNOWN"


@dataclass(frozen=True)
class _Token:
    symbol: str
    decimals: int


class EvmRpcSource:
    def __init__(self, chain: str, rpc: JsonRpc, *, span: int = 50_000) -> None:
        meta = get_chain(chain)
        if meta.kind is not ChainKind.EVM:
            raise ChainSourceError("the EVM source only serves EVM chains")
        self.chain = chain
        self._native = meta.native_symbol
        self._decimals = meta.native_decimals
        self._rpc = rpc
        self._span = span
        self._tokens: dict[str, _Token | None] = {}

    @classmethod
    def from_url(cls, chain: str, url: str) -> EvmRpcSource:
        return cls(chain, HttpJsonRpc(url))

    # ------------------------------------------------------------------ helpers
    async def _token(self, contract: str) -> _Token | None:
        if contract not in self._tokens:
            try:
                decimals = _int(
                    await self._rpc.call(
                        "eth_call", [{"to": contract, "data": SELECTOR_DECIMALS}, "latest"]
                    )
                )
                symbol = decode_abi_string(
                    await self._rpc.call(
                        "eth_call", [{"to": contract, "data": SELECTOR_SYMBOL}, "latest"]
                    )
                )
                self._tokens[contract] = (
                    _Token(clean_symbol(symbol), decimals) if decimals <= 18 else None
                )
            except (ChainSourceError, ValueError):
                self._tokens[contract] = None
        return self._tokens[contract]

    async def _block_time(self, number: int, cache: dict[int, datetime]) -> datetime:
        if number not in cache:
            block = await self._rpc.call("eth_getBlockByNumber", [hex(number), False])
            cache[number] = datetime.fromtimestamp(_int(block["timestamp"]), UTC)
        return cache[number]

    async def _fee(self, tx_hash: str) -> Decimal:
        receipt = await self._rpc.call("eth_getTransactionReceipt", [tx_hash])
        wei = _int(receipt["gasUsed"]) * _int(receipt.get("effectiveGasPrice")) + _int(
            receipt.get("l1Fee")
        )
        return from_base_units(wei, self._decimals)

    def _windows(self, start: int, latest: int) -> list[tuple[int, int]]:
        return [(a, min(a + self._span - 1, latest)) for a in range(start, latest + 1, self._span)]

    # ------------------------------------------------------------------ port
    async def balances(self, addresses: Sequence[str]) -> list[AddressBalance]:
        out: list[AddressBalance] = []
        for address in addresses:
            wei = _int(await self._rpc.call("eth_getBalance", [address, "latest"]))
            out.append(
                AddressBalance(address, self._native, "", from_base_units(wei, self._decimals))
            )
            for contract, token in sorted(self._tokens.items()):
                if token is None:
                    continue
                data = SELECTOR_BALANCE_OF + "0" * 24 + address[2:].lower()
                raw = _int(
                    await self._rpc.call("eth_call", [{"to": contract, "data": data}, "latest"])
                )
                if raw:
                    out.append(
                        AddressBalance(
                            address, token.symbol, contract, from_base_units(raw, token.decimals)
                        )
                    )
        return out

    async def history(
        self, addresses: Sequence[str], *, since_height: int | None = None
    ) -> list[ChainTransfer]:
        watched = {a.lower(): a for a in addresses}
        latest = _int(await self._rpc.call("eth_blockNumber", []))
        windows = self._windows(since_height or 0, latest)
        times: dict[int, datetime] = {}
        transfers = await self._native_history(watched, windows, times)
        transfers += await self._token_history(watched, windows, times)
        return transfers

    async def _native_history(
        self, watched: dict[str, str], windows: list[tuple[int, int]], times: dict[int, datetime]
    ) -> list[ChainTransfer]:
        traces: dict[tuple[str, tuple[int, ...]], dict[str, Any]] = {}
        for first, last in windows:
            for side in ("fromAddress", "toAddress"):
                query = {
                    "fromBlock": hex(first),
                    "toBlock": hex(last),
                    side: list(watched.values()),
                }
                try:
                    found = await self._rpc.call("trace_filter", [query])
                except MethodNotFoundError:
                    raise HistoryUnavailable(
                        "native history needs a node with trace_filter (Erigon, Reth, Nethermind)"
                    ) from None
                for trace in found or []:
                    key = (trace["transactionHash"].lower(), tuple(trace.get("traceAddress", [])))
                    traces[key] = trace

        by_tx: dict[str, list[tuple[tuple[int, ...], dict[str, Any]]]] = {}
        for (tx_hash, path), trace in traces.items():
            by_tx.setdefault(tx_hash, []).append((path, trace))

        out: list[ChainTransfer] = []
        for tx_hash, entries in sorted(by_tx.items()):
            entries.sort(key=lambda e: e[0])
            fee_paid = False
            for path, trace in entries:
                event_index = trace_event_index(path)
                action = trace.get("action", {})
                kind = trace.get("type")
                if kind == "suicide":
                    sender, receiver = action.get("address"), action.get("refundAddress")
                    value = _int(action.get("balance"))
                elif kind == "create":
                    sender = action.get("from")
                    receiver = (trace.get("result") or {}).get("address")
                    value = _int(action.get("value"))
                else:
                    sender, receiver = action.get("from"), action.get("to")
                    value = _int(action.get("value"))
                if trace.get("error"):
                    value = 0
                number = _int(trace["blockNumber"])
                common = {
                    "tx_hash": tx_hash,
                    "asset": self._native,
                    "asset_contract": "",
                    "event_index": event_index,
                    "block_height": number,
                    "block_time": await self._block_time(number, times),
                }
                sender_mine = sender is not None and sender.lower() in watched
                receiver_mine = receiver is not None and receiver.lower() in watched
                top_level_sender = sender_mine and not path
                if (sender_mine and value > 0) or top_level_sender:
                    fee = await self._fee(tx_hash) if top_level_sender else None
                    fee_paid = fee_paid or top_level_sender
                    out.append(
                        ChainTransfer(
                            address=watched[str(sender).lower()],
                            direction="out",
                            quantity=-from_base_units(value, self._decimals),
                            counterparty=str(receiver) if receiver else None,
                            fee_asset=self._native if fee is not None else None,
                            fee_qty=fee,
                            **common,  # type: ignore[arg-type]
                        )
                    )
                if receiver_mine and value > 0:
                    out.append(
                        ChainTransfer(
                            address=watched[str(receiver).lower()],
                            direction="in",
                            quantity=from_base_units(value, self._decimals),
                            counterparty=str(sender) if sender else None,
                            **common,  # type: ignore[arg-type]
                        )
                    )
        return out

    async def _token_history(
        self, watched: dict[str, str], windows: list[tuple[int, int]], times: dict[int, datetime]
    ) -> list[ChainTransfer]:
        padded = [_pad(a) for a in watched.values()]
        logs: dict[tuple[str, int], dict[str, Any]] = {}
        for first, last in windows:
            window = {"fromBlock": hex(first), "toBlock": hex(last)}
            for topics in ([TRANSFER_TOPIC, None, padded], [TRANSFER_TOPIC, padded]):
                for log in (
                    await self._rpc.call("eth_getLogs", [{**window, "topics": topics}]) or []
                ):
                    logs[(log["transactionHash"].lower(), _int(log["logIndex"]))] = log

        out: list[ChainTransfer] = []
        for (tx_hash, log_index), log in sorted(logs.items()):
            if len(log["topics"]) != 3:  # ERC-721 shares the topic with four
                continue
            contract = normalise_evm_address(log["address"]).lower()
            token = await self._token(contract)
            if token is None:
                continue
            try:
                amount = from_base_units(_int(log["data"]), token.decimals)
            except CryptoInputError:
                continue
            sender, receiver = _topic_address(log["topics"][1]), _topic_address(log["topics"][2])
            number = _int(log["blockNumber"])
            common = {
                "tx_hash": tx_hash,
                "asset": token.symbol,
                "asset_contract": contract,
                "event_index": log_index,
                "block_height": number,
                "block_time": await self._block_time(number, times),
            }
            if sender.lower() in watched:
                out.append(
                    ChainTransfer(
                        address=watched[sender.lower()],
                        direction="out",
                        quantity=-amount,
                        counterparty=receiver,
                        **common,  # type: ignore[arg-type]
                    )
                )
            if receiver.lower() in watched:
                out.append(
                    ChainTransfer(
                        address=watched[receiver.lower()],
                        direction="in",
                        quantity=amount,
                        counterparty=sender,
                        **common,  # type: ignore[arg-type]
                    )
                )
        return out
