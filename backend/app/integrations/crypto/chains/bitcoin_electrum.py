"""Bitcoin through your own Electrum-protocol server (electrs or Fulcrum), ADR 0020.

The protocol is newline-delimited JSON-RPC over TCP or TLS, so a small client replaces a library.
Per address we ask the server for its history (``blockchain.scripthash.get_history``), then read
each transaction (``blockchain.transaction.get``) and its funding transactions to find the
movements:

- an output paying a watched address is an ``in`` movement (``event_index`` = output index);
- an input spending an output that paid a watched address is an ``out`` movement of that output's
  full value (``event_index`` = input index). Change comes back as an ``in`` at the change address,
  so the sum of a wallet's movements equals its balance and the fee is implicit in the difference.

All values are integer satoshi until :func:`from_base_units`. The server sees only script hashes of
the addresses of this wallet, which is why it should be your own.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import ssl
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from embit.networks import NETWORKS
from embit.script import address_to_scriptpubkey
from embit.transaction import Transaction

from app.domain.crypto.units import from_base_units
from app.integrations.crypto.chains.base import AddressBalance, ChainSourceError, ChainTransfer
from app.integrations.crypto.chains.endpoint import is_own_network

NATIVE = ""
TIMEOUT_SECONDS = 30


class ElectrumTransport(Protocol):
    async def request(self, method: str, params: list[Any]) -> Any: ...


class TcpElectrumTransport:
    """One connection per request batch is enough for a sync; kept simple and bounded."""

    def __init__(self, host: str, port: int, *, tls: bool, verify_tls: bool = True) -> None:
        self._host, self._port, self._tls, self._verify = host, port, tls, verify_tls
        self._next_id = 0
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def _connect(self) -> None:
        context = ssl.create_default_context() if self._tls else None
        if context is not None and not self._verify:
            # Your own server on your own network usually runs a self-signed certificate. A
            # provider outside it must present one that verifies.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port, ssl=context), TIMEOUT_SECONDS
        )

    async def request(self, method: str, params: list[Any]) -> Any:
        async with self._lock:
            if self._writer is None or self._reader is None:
                try:
                    await self._connect()
                except (OSError, TimeoutError):
                    raise ChainSourceError("could not reach the Electrum server") from None
            if self._reader is None or self._writer is None:
                raise ChainSourceError("could not reach the Electrum server")
            self._next_id += 1
            call = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
            try:
                self._writer.write(json.dumps(call).encode() + b"\n")
                await self._writer.drain()
                while True:
                    line = await asyncio.wait_for(self._reader.readline(), TIMEOUT_SECONDS)
                    if not line:
                        raise ChainSourceError("the Electrum server closed the connection")
                    reply = json.loads(line)
                    if reply.get("id") == self._next_id:
                        break
            except (OSError, TimeoutError, ValueError):
                self._writer = None
                raise ChainSourceError("the Electrum server did not answer") from None
            if reply.get("error"):
                raise ChainSourceError("the Electrum server refused the request")
            return reply.get("result")


def scripthash(address: str) -> str:
    """Electrum's index key: SHA-256 of the output script, byte-reversed, hex."""
    script = address_to_scriptpubkey(address).data
    return hashlib.sha256(script).digest()[::-1].hex()


class ElectrumSource:
    def __init__(self, chain: str, transport: ElectrumTransport, *, network: str = "main") -> None:
        if chain != "bitcoin":
            raise ChainSourceError("the Electrum source only serves bitcoin")
        self.chain = chain
        self._transport = transport
        self._net = NETWORKS[network]

    @classmethod
    def from_url(cls, chain: str, url: str, *, network: str = "main") -> ElectrumSource:
        parts = urlsplit(url)
        tls = parts.scheme == "ssl"
        port = parts.port or (50002 if tls else 50001)
        if not parts.hostname:
            raise ChainSourceError("the Electrum endpoint has no host")
        transport = TcpElectrumTransport(
            parts.hostname, port, tls=tls, verify_tls=is_own_network(parts.hostname)
        )
        return cls(chain, transport, network=network)

    async def balances(self, addresses: Sequence[str]) -> list[AddressBalance]:
        out: list[AddressBalance] = []
        for address in addresses:
            reply = await self._transport.request(
                "blockchain.scripthash.get_balance", [scripthash(address)]
            )
            satoshi = int(reply["confirmed"]) + int(reply["unconfirmed"])
            out.append(AddressBalance(address, "BTC", NATIVE, from_base_units(max(satoshi, 0), 8)))
        return out

    async def _tx(self, txid: str, cache: dict[str, Transaction]) -> Transaction:
        if txid not in cache:
            raw = await self._transport.request("blockchain.transaction.get", [txid])
            cache[txid] = Transaction.parse(bytes.fromhex(raw))
        return cache[txid]

    async def _block_time(self, height: int, cache: dict[int, datetime]) -> datetime:
        if height not in cache:
            header = await self._transport.request("blockchain.block.header", [height])
            stamp = int.from_bytes(bytes.fromhex(header)[68:72], "little")
            cache[height] = datetime.fromtimestamp(stamp, UTC)
        return cache[height]

    def _address_of(self, script: Any) -> str | None:
        try:
            return str(script.address(self._net))
        except Exception:
            return None

    async def history(
        self, addresses: Sequence[str], *, since_height: int | None = None
    ) -> list[ChainTransfer]:
        watched = set(addresses)
        txs: dict[str, Transaction] = {}
        times: dict[int, datetime] = {}
        heights: dict[str, int] = {}
        known: set[str] = set()  # every transaction in our history, even before ``since_height``
        for address in addresses:
            entries = await self._transport.request(
                "blockchain.scripthash.get_history", [scripthash(address)]
            )
            for entry in entries:
                height = int(entry["height"])  # 0 or -1: unconfirmed
                known.add(str(entry["tx_hash"]).lower())
                if since_height is not None and 0 < height < since_height:
                    continue
                heights[str(entry["tx_hash"]).lower()] = height

        transfers: list[ChainTransfer] = []
        for txid, height in sorted(heights.items(), key=lambda kv: (kv[1], kv[0])):
            tx = await self._tx(txid, txs)
            confirmed = height > 0
            when = await self._block_time(height, times) if confirmed else None
            common = {"block_height": height if confirmed else None, "block_time": when}
            for index, vout in enumerate(tx.vout):
                owner = self._address_of(vout.script_pubkey)
                if owner in watched and vout.value > 0:
                    transfers.append(
                        ChainTransfer(
                            tx_hash=txid,
                            address=str(owner),
                            direction="in",
                            asset="BTC",
                            asset_contract=NATIVE,
                            quantity=from_base_units(int(vout.value), 8),
                            event_index=index,
                            **common,  # type: ignore[arg-type]
                        )
                    )
            for index, vin in enumerate(tx.vin):
                # An input can only spend one of our outputs if the transaction that created that
                # output is in our history, so every other input is skipped without a fetch.
                if vin.txid.hex() not in known:
                    continue
                previous = await self._tx(vin.txid.hex(), txs)
                spent = previous.vout[vin.vout]
                owner = self._address_of(spent.script_pubkey)
                if owner in watched and spent.value > 0:
                    transfers.append(
                        ChainTransfer(
                            tx_hash=txid,
                            address=str(owner),
                            direction="out",
                            asset="BTC",
                            asset_contract=NATIVE,
                            quantity=-from_base_units(int(spent.value), 8),
                            event_index=index,
                            **common,  # type: ignore[arg-type]
                        )
                    )
        return transfers
