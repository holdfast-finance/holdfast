"""Electrum adapter against a scripted transport and synthetic transactions built with embit.

No server, no socket. The funding and spending transactions pay published BIP84 test-vector
addresses and an obviously invented external address.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

import pytest
from embit.networks import NETWORKS
from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from app.integrations.crypto.chains.bitcoin_electrum import ElectrumSource, scripthash

A = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"  # BIP84 receive 0
B = "bc1q8c6fshw2dlwun7ekn9qwf37cu2rn755upcp6el"  # BIP84 change 0
OTHER = "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"  # stands for someone else
BTC = Decimal


def script(address: str) -> Any:
    return address_to_scriptpubkey(address)


def header(timestamp: int) -> str:
    return (b"\x00" * 68 + timestamp.to_bytes(4, "little") + b"\x00" * 8).hex()


def sat(btc: str) -> int:
    return int(BTC(btc) * 10**8)


FUNDING = Transaction(
    vin=[TransactionInput(b"\x07" * 32, 0)],
    vout=[TransactionOutput(sat("0.5"), script(A)), TransactionOutput(sat("0.2"), script(OTHER))],
)
SPEND = Transaction(
    vin=[TransactionInput(FUNDING.txid(), 0)],
    vout=[
        TransactionOutput(sat("0.3"), script(OTHER)),
        TransactionOutput(sat("0.1999"), script(B)),
    ],
)
FUNDING_ID, SPEND_ID = FUNDING.txid().hex(), SPEND.txid().hex()


class ScriptedElectrum:
    """Answers like electrs for A (funded at 800000, spent at 800010) and B (change)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Any]]] = []
        self.history = {
            scripthash(A): [
                {"tx_hash": FUNDING_ID, "height": 800000},
                {"tx_hash": SPEND_ID, "height": 800010},
            ],
            scripthash(B): [{"tx_hash": SPEND_ID, "height": 800010}],
        }
        self.balance = {
            scripthash(A): {"confirmed": 0, "unconfirmed": 0},
            scripthash(B): {"confirmed": sat("0.1999"), "unconfirmed": 0},
        }

    async def request(self, method: str, params: list[Any]) -> Any:
        self.calls.append((method, params))
        if method == "blockchain.scripthash.get_history":
            return self.history[params[0]]
        if method == "blockchain.scripthash.get_balance":
            return self.balance[params[0]]
        if method == "blockchain.transaction.get":
            return {FUNDING_ID: FUNDING, SPEND_ID: SPEND}[params[0]].serialize().hex()
        if method == "blockchain.block.header":
            return header({800000: 1_700_000_000, 800010: 1_700_006_000}[params[0]])
        raise AssertionError(method)


def source(transport: ScriptedElectrum | None = None) -> ElectrumSource:
    return ElectrumSource("bitcoin", transport or ScriptedElectrum())


def test_scripthash_is_the_reversed_sha256_of_the_output_script() -> None:
    raw = bytes.fromhex("0014c0cebcd6c3d3ca8c75dc5ec62ebe55330ef910e2")
    assert scripthash(A) == hashlib.sha256(raw).digest()[::-1].hex()


def test_synthetic_transactions_round_trip_with_their_ids() -> None:
    parsed = Transaction.parse(SPEND.serialize())
    assert parsed.vin[0].txid.hex() == FUNDING_ID
    assert parsed.vout[1].script_pubkey.address(NETWORKS["main"]) == B


async def test_history_finds_inflows_outflows_and_change() -> None:
    transfers = await source().history([A, B])
    got = [(t.tx_hash[:6], t.address, t.direction, t.quantity, t.event_index) for t in transfers]
    assert len(got) == 3
    by_key = {(t.tx_hash, t.address, t.direction): t for t in transfers}
    assert by_key[(FUNDING_ID, A, "in")].quantity == BTC("0.50000000")
    assert by_key[(FUNDING_ID, A, "in")].event_index == 0
    assert by_key[(SPEND_ID, A, "out")].quantity == BTC("-0.50000000")
    assert by_key[(SPEND_ID, B, "in")].quantity == BTC("0.19990000")
    assert by_key[(SPEND_ID, B, "in")].event_index == 1  # the output index
    assert len(transfers) == 3  # the 0.3 and 0.2 outputs belong to someone else


async def test_signs_follow_the_convention_and_the_sum_equals_the_balance() -> None:
    s = source()
    transfers = await s.history([A, B])
    assert all(t.quantity > 0 for t in transfers if t.direction == "in")
    assert all(t.quantity < 0 for t in transfers if t.direction == "out")
    balances = {b.address: b.quantity for b in await s.balances([A, B])}
    for address in (A, B):
        assert (
            sum((t.quantity for t in transfers if t.address == address), BTC(0))
            == balances[address]
        )
    assert balances == {A: BTC("0"), B: BTC("0.1999")}


async def test_block_times_come_from_the_header_and_amounts_are_exact_decimals() -> None:
    transfers = await source().history([A])
    funding = next(t for t in transfers if t.tx_hash == FUNDING_ID)
    assert funding.block_height == 800000
    assert funding.block_time is not None and int(funding.block_time.timestamp()) == 1_700_000_000
    assert isinstance(funding.quantity, Decimal) and funding.asset == "BTC"
    assert funding.asset_contract == ""


async def test_unconfirmed_transactions_have_no_height_or_time() -> None:
    t = ScriptedElectrum()
    t.history[scripthash(A)] = [{"tx_hash": FUNDING_ID, "height": 0}]
    transfers = await source(t).history([A])
    assert [(x.block_height, x.block_time) for x in transfers] == [(None, None)]
    assert not any(c[0] == "blockchain.block.header" for c in t.calls)


async def test_since_height_skips_older_history() -> None:
    transfers = await source().history([A], since_height=800005)
    assert {t.tx_hash for t in transfers} == {SPEND_ID}


async def test_the_server_is_only_asked_about_script_hashes_never_plain_addresses() -> None:
    t = ScriptedElectrum()
    await source(t).history([A])
    asked = [
        p
        for method, params in t.calls
        if method.startswith("blockchain.scripthash")
        for p in params
    ]
    assert asked == [scripthash(A)] and A not in asked


def test_only_bitcoin_is_served() -> None:
    from app.integrations.crypto.chains.base import ChainSourceError

    with pytest.raises(ChainSourceError):
        ElectrumSource("ethereum", ScriptedElectrum())
