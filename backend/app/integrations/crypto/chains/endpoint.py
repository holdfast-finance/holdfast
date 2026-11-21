"""A household's own chain endpoint, and the privacy gate in front of every adapter (ADR 0020).

There is **no default endpoint** anywhere. A real adapter is built only from an
:class:`EndpointConfig` the household saved. A host that is not the household's own machine or
network (loopback, private ranges, Tailscale, ``.local``, ``.onion``, a single-label name) is
treated as a public provider: it learns every address in the wallet, so it needs ``allow_public``
and an acknowledged privacy warning, both set by the user. No DNS lookup is made here.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from app.integrations.crypto.chains.base import ChainSource, ChainSourceError

PRIVACY_WARNING = (
    "This provider is not on your own network. It will learn every address in this wallet and "
    "when you look at it. Use your own node if you can."
)
_PRIVATE_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".ts.net", ".onion", ".localhost")
# Only the ranges a household's own network uses. ``ipaddress.is_private`` also covers documentation
# and reserved ranges, which are not "yours".
_OWN_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",  # Tailscale / CGNAT
        "fc00::/7",
        "fe80::/10",
    )
)


class EndpointKind(StrEnum):
    ELECTRUM = "electrum"  # electrs / Fulcrum, tcp:// or ssl://
    EVM_RPC = "evm_rpc"  # JSON-RPC over http(s)://


@dataclass(frozen=True)
class EndpointConfig:
    chain: str
    kind: EndpointKind
    url: str
    allow_public: bool = False
    privacy_warning_acknowledged: bool = False

    def __repr__(self) -> str:  # the URL can carry an API key: never print it
        return f"EndpointConfig(chain={self.chain!r}, kind={self.kind.value!r}, url=<hidden>)"


def is_own_network(host: str) -> bool:
    """True when ``host`` is on the household's own machine or network. Pure: no DNS."""
    name = host.strip("[]").lower().rstrip(".")
    if not name:
        return False
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return "." not in name or name.endswith(_PRIVATE_SUFFIXES)
    return ip.is_loopback or any(ip in net for net in _OWN_NETWORKS if net.version == ip.version)


def check_endpoint(config: EndpointConfig) -> None:
    """Raise :class:`ChainSourceError` (fixed text, no URL) unless this endpoint may be used."""
    parts = urlsplit(config.url)
    expected = {
        EndpointKind.ELECTRUM: {"tcp", "ssl"},
        EndpointKind.EVM_RPC: {"http", "https"},
    }[config.kind]
    if parts.scheme not in expected or not parts.hostname:
        raise ChainSourceError("the chain endpoint is not a valid URL for this kind of source")
    if not is_own_network(parts.hostname) and not (
        config.allow_public and config.privacy_warning_acknowledged
    ):
        raise ChainSourceError(
            "this endpoint is a public provider; confirm the privacy warning to use it"
        )


def build_chain_source(config: EndpointConfig) -> ChainSource:
    """Construct the real adapter for a configured endpoint (after the privacy gate)."""
    check_endpoint(config)
    if config.kind is EndpointKind.ELECTRUM:
        from app.integrations.crypto.chains.bitcoin_electrum import ElectrumSource

        return ElectrumSource.from_url(config.chain, config.url)
    from app.integrations.crypto.chains.evm_rpc import EvmRpcSource

    source: ChainSource = EvmRpcSource.from_url(config.chain, config.url)
    return source
