"""The privacy gate: no default endpoint, and a public provider needs an acknowledged warning."""

from __future__ import annotations

import pytest

from app.integrations.crypto.chains.base import ChainSource, ChainSourceError
from app.integrations.crypto.chains.bitcoin_electrum import ElectrumSource
from app.integrations.crypto.chains.endpoint import (
    EndpointConfig,
    EndpointKind,
    build_chain_source,
    check_endpoint,
    is_own_network,
)
from app.integrations.crypto.chains.evm_rpc import EvmRpcSource
from app.integrations.crypto.chains.fake import FakeChainSource


@pytest.mark.parametrize(
    ("host", "own"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("192.168.1.20", True),
        ("10.0.0.5", True),
        ("172.16.4.4", True),
        ("100.101.102.103", True),  # Tailscale
        ("fulcrum", True),  # single label, a container or LAN name
        ("umbrel.local", True),
        ("node.example-tailnet.ts.net", True),
        ("abcdefghijklmnop.onion", True),
        ("electrum.example.com", False),
        ("8.8.8.8", False),
        ("203.0.113.9", False),
        ("", False),
    ],
)
def test_own_network_detection_needs_no_dns(host: str, own: bool) -> None:
    assert is_own_network(host) is own


def test_a_public_endpoint_is_refused_until_the_warning_is_acknowledged() -> None:
    url = "https://rpc.example.com/v1/abc123"
    base = EndpointConfig("ethereum", EndpointKind.EVM_RPC, url)
    with pytest.raises(ChainSourceError, match="public provider"):
        check_endpoint(base)
    with pytest.raises(ChainSourceError):
        check_endpoint(EndpointConfig("ethereum", EndpointKind.EVM_RPC, url, allow_public=True))
    with pytest.raises(ChainSourceError):
        check_endpoint(
            EndpointConfig("ethereum", EndpointKind.EVM_RPC, url, privacy_warning_acknowledged=True)
        )
    check_endpoint(
        EndpointConfig(
            "ethereum",
            EndpointKind.EVM_RPC,
            url,
            allow_public=True,
            privacy_warning_acknowledged=True,
        )
    )


@pytest.mark.parametrize(
    ("kind", "url"),
    [
        (EndpointKind.ELECTRUM, "http://127.0.0.1:50001"),
        (EndpointKind.EVM_RPC, "tcp://127.0.0.1:8545"),
        (EndpointKind.EVM_RPC, "not a url"),
        (EndpointKind.EVM_RPC, "http://"),
    ],
)
def test_the_url_scheme_must_fit_the_kind_of_source(kind: EndpointKind, url: str) -> None:
    with pytest.raises(ChainSourceError) as err:
        check_endpoint(EndpointConfig("bitcoin", kind, url))
    assert url not in str(err.value) or url == ""


def test_error_and_repr_never_contain_the_url() -> None:
    canary_url = "https://rpc.example.com/v1/canary-path-segment"
    config = EndpointConfig("ethereum", EndpointKind.EVM_RPC, canary_url)
    assert "canary-path-segment" not in repr(config) and "canary-path-segment" not in str(config)
    with pytest.raises(ChainSourceError) as err:
        build_chain_source(config)
    assert "canary-path-segment" not in str(err.value)


def test_own_network_endpoints_build_real_adapters_without_touching_the_network() -> None:
    electrum = build_chain_source(
        EndpointConfig("bitcoin", EndpointKind.ELECTRUM, "tcp://192.168.1.20:50001")
    )
    evm = build_chain_source(EndpointConfig("base", EndpointKind.EVM_RPC, "http://127.0.0.1:8545"))
    assert isinstance(electrum, ElectrumSource) and isinstance(evm, EvmRpcSource)
    assert electrum.chain == "bitcoin" and evm.chain == "base"
    assert isinstance(FakeChainSource("bitcoin"), ChainSource)  # the fake satisfies the port


def test_adapters_refuse_a_chain_they_do_not_serve() -> None:
    with pytest.raises(ChainSourceError):
        build_chain_source(
            EndpointConfig("ethereum", EndpointKind.ELECTRUM, "tcp://10.0.0.1:50001")
        )
    with pytest.raises(ChainSourceError):
        build_chain_source(EndpointConfig("bitcoin", EndpointKind.EVM_RPC, "http://10.0.0.1:8545"))
