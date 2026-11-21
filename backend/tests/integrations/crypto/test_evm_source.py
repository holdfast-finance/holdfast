"""EVM JSON-RPC adapter against a scripted node. No socket; every address is an EIP-55 vector.

The scripted node answers ``trace_filter``, ``eth_getLogs``, receipts, blocks and ``eth_call`` the
way Erigon or Reth would, including a reverted transaction (gas paid, no value), an internal
transfer, an ERC-721 log (ignored) and a token that has no ``decimals()`` (skipped).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.integrations.crypto.chains.base import ChainSourceError, HistoryUnavailable
from app.integrations.crypto.chains.evm_rpc import (
    TRANSFER_TOPIC,
    EvmRpcSource,
    HttpJsonRpc,
    MethodNotFoundError,
    clean_symbol,
    decode_abi_string,
    trace_event_index,
)

W = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
OTHER = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"
CONTRACT = "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB"
BROKEN = "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb"
ETH = 10**18


def h(n: int) -> str:
    return "0x" + f"{n:064x}"


def topic(address: str) -> str:
    return "0x" + "0" * 24 + address[2:].lower()


def trace(
    tx: int, block: int, sender: str, to: str, value: int, path: list[int], **extra: Any
) -> dict[str, Any]:
    return {
        "type": "call",
        "action": {"from": sender.lower(), "to": to.lower(), "value": hex(value)},
        "transactionHash": h(tx),
        "blockNumber": block,
        "traceAddress": path,
        **extra,
    }


TRACES = [
    trace(0x11, 10, W, OTHER, 1 * ETH, []),
    trace(0x22, 20, OTHER, W, 2 * ETH, []),
    trace(0x33, 30, W, CONTRACT, 0, [], error="Reverted"),
    trace(0x44, 40, OTHER, CONTRACT, 0, []),
    trace(0x44, 40, CONTRACT, W, ETH // 2, [0]),
]
GAS = {h(0x11): (21_000, 10_000_000_000), h(0x33): (50_000, 10_000_000_000)}


def abi_string(text: str) -> str:
    data = text.encode()
    return (
        "0x"
        + (32).to_bytes(32, "big").hex()
        + len(data).to_bytes(32, "big").hex()
        + data.hex().ljust(64, "0")
    )


def log(
    tx: int, index: int, block: int, sender: str, to: str, raw: int, topics: int = 3
) -> dict[str, Any]:
    t = [TRANSFER_TOPIC, topic(sender), topic(to)]
    if topics == 4:
        t.append(h(7))  # an ERC-721 token id
    return {
        "address": CONTRACT.lower(),
        "topics": t,
        "data": h(raw) if topics == 3 else "0x",
        "blockNumber": hex(block),
        "transactionHash": h(tx),
        "logIndex": hex(index),
    }


LOGS = [
    log(0x51, 5, 50, OTHER, W, 100_000_000),
    log(0x52, 2, 60, W, OTHER, 40_000_000),
    log(0x53, 1, 70, OTHER, W, 0, topics=4),
]


class ScriptedNode:
    def __init__(self, *, trace_filter: bool = True, l1_fee: int = 0) -> None:
        self.trace_filter = trace_filter
        self.l1_fee = l1_fee
        self.calls: list[str] = []

    async def call(self, method: str, params: list[Any]) -> Any:
        self.calls.append(method)
        if method == "eth_blockNumber":
            return "0x64"
        if method == "trace_filter":
            if not self.trace_filter:
                raise MethodNotFoundError("nope")
            query = params[0]
            side, wanted = next((k, v) for k, v in query.items() if k.endswith("Address"))
            key = "from" if side == "fromAddress" else "to"
            wanted = {a.lower() for a in wanted}
            return [t for t in TRACES if t["action"].get(key) in wanted]
        if method == "eth_getTransactionReceipt":
            used, price = GAS[params[0]]
            receipt: dict[str, Any] = {"gasUsed": hex(used), "effectiveGasPrice": hex(price)}
            if self.l1_fee:
                receipt["l1Fee"] = hex(self.l1_fee)
            return receipt
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(1_700_000_000 + int(params[0], 16))}
        if method == "eth_getLogs":
            topics = params[0]["topics"]
            if len(topics) == 3:
                return [x for x in LOGS if x["topics"][2] in topics[2]]
            return [x for x in LOGS if x["topics"][1] in topics[1]]
        if method == "eth_call":
            to, data = params[0]["to"].lower(), params[0]["data"]
            if to == BROKEN.lower():
                raise ChainSourceError("execution reverted")
            if data.startswith("0x313ce567"):
                return h(6)
            if data.startswith("0x95d89b41"):
                return abi_string("USDC")
            if data.startswith("0x70a08231"):
                return h(60_000_000)
        if method == "eth_getBalance":
            return hex(3 * ETH + 1)
        raise AssertionError(method)


def source(node: ScriptedNode | None = None, chain: str = "ethereum") -> EvmRpcSource:
    return EvmRpcSource(chain, node or ScriptedNode(), span=1000)


async def test_native_movements_with_gas_and_internal_transfers() -> None:
    transfers = [t for t in await source().history([W]) if t.asset == "ETH"]
    by_tx = {(t.tx_hash, t.direction): t for t in transfers}
    sent = by_tx[(h(0x11), "out")]
    assert sent.quantity == Decimal("-1") and sent.event_index == 0
    assert sent.fee_asset == "ETH" and sent.fee_qty == Decimal("0.000210000000000000")
    assert sent.counterparty is not None and sent.counterparty.lower() == OTHER.lower()
    assert by_tx[(h(0x22), "in")].quantity == Decimal("2")
    assert by_tx[(h(0x22), "in")].fee_qty is None  # the sender pays, not us
    reverted = by_tx[(h(0x33), "out")]
    assert reverted.quantity == Decimal("0") and reverted.fee_qty == Decimal("0.0005")
    internal = by_tx[(h(0x44), "in")]
    assert internal.quantity == Decimal("0.5") and internal.event_index == 1
    assert len(transfers) == 4


async def test_signs_and_exact_wei() -> None:
    transfers = await source().history([W])
    assert all(t.quantity >= 0 for t in transfers if t.direction == "in")
    assert all(t.quantity <= 0 for t in transfers if t.direction == "out")
    node = ScriptedNode()
    node_traces = [trace(0x66, 5, OTHER, W, 1, [])]
    original = list(TRACES)
    TRACES[:] = node_traces
    try:
        one_wei = [t for t in await source(node).history([W]) if t.tx_hash == h(0x66)]
    finally:
        TRACES[:] = original
    assert one_wei[0].quantity == Decimal("0.000000000000000001")


async def test_the_op_stack_l1_fee_is_added_to_gas() -> None:
    transfers = await source(ScriptedNode(l1_fee=1_000), chain="base").history([W])
    sent = next(t for t in transfers if t.tx_hash == h(0x11) and t.direction == "out")
    assert sent.fee_qty == Decimal("0.000210000000001000")


async def test_erc20_logs_decode_token_units_and_skip_nfts_and_broken_contracts() -> None:
    tokens = [t for t in await source().history([W]) if t.asset_contract]
    by = {t.direction: t for t in tokens}
    assert by["in"].quantity == Decimal("100.000000") and by["in"].asset == "USDC"
    assert by["out"].quantity == Decimal("-40.000000")
    assert by["in"].asset_contract == CONTRACT.lower()
    assert (by["in"].event_index, by["out"].event_index) == (5, 2)
    assert len(tokens) == 2  # the four-topic ERC-721 log is ignored


async def test_a_token_without_decimals_is_skipped() -> None:
    bad = {**LOGS[0], "address": BROKEN.lower(), "transactionHash": h(0x77)}
    LOGS.append(bad)
    try:
        tokens = [t for t in await source().history([W]) if t.asset_contract]
    finally:
        LOGS.remove(bad)
    assert {t.asset_contract for t in tokens} == {CONTRACT.lower()}


async def test_a_node_without_trace_filter_says_so_instead_of_guessing() -> None:
    with pytest.raises(HistoryUnavailable, match="trace_filter"):
        await source(ScriptedNode(trace_filter=False)).history([W])


async def test_balances_include_tokens_seen_in_history() -> None:
    s = source()
    await s.history([W])
    got = {(b.asset, b.asset_contract): b.quantity for b in await s.balances([W])}
    assert got[("ETH", "")] == Decimal("3.000000000000000001")
    assert got[("USDC", CONTRACT.lower())] == Decimal("60.000000")


async def test_history_is_windowed_by_block_span() -> None:
    node = ScriptedNode()
    await EvmRpcSource("ethereum", node, span=40).history([W])
    assert node.calls.count("trace_filter") == 2 * 3  # blocks 0..100 in windows of 40, two sides


def test_trace_event_index_depends_only_on_the_trace_not_on_the_filter() -> None:
    assert [trace_event_index(p) for p in [(), (0,), (1,), (0, 0), (0, 1)]] == [0, 1, 2, 1001, 1002]
    assert (
        len({trace_event_index((a, b, c)) for a in range(5) for b in range(5) for c in range(5)})
        == 125
    )
    assert trace_event_index((5,) * 12) < 2_000_000_000


def test_abi_helpers_and_symbol_cleaning() -> None:
    assert decode_abi_string(abi_string("USDC")) == "USDC"
    legacy = "0x" + b"MKR".ljust(32, b"\x00").hex()  # bytes32 symbol, as old tokens return
    assert decode_abi_string(legacy) == "MKR"
    assert clean_symbol("usd c!") == "USDC"
    assert clean_symbol("   ") == "UNKNOWN"
    assert clean_symbol("a" * 80) == "A" * 32
    assert TRANSFER_TOPIC == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


async def test_http_transport_hides_the_url_and_maps_method_not_found() -> None:
    canary = "canary-path-segment"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("fail"):
            return httpx.Response(500, json={})
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "x"}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    failing = HttpJsonRpc(f"http://127.0.0.1:8545/{canary}/fail", client)
    with pytest.raises(ChainSourceError) as err:
        await failing.call("eth_blockNumber", [])
    assert canary not in str(err.value) and canary not in repr(err.value)
    assert err.value.__cause__ is None
    with pytest.raises(MethodNotFoundError):
        await HttpJsonRpc("http://127.0.0.1:8545/ok", client).call("trace_filter", [])
    await client.aclose()


def test_only_evm_chains_are_served() -> None:
    with pytest.raises(ChainSourceError):
        EvmRpcSource("bitcoin", ScriptedNode())
