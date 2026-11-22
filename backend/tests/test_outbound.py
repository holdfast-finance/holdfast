"""Outbound URL validator: https only, no private/loopback/link-local/multicast/reserved targets."""

from __future__ import annotations

import ipaddress

import httpx
import pytest

from app.security.outbound import OutboundRejected, blocked_reason, resolve_outbound


def _resolver(*answers: str):  # type: ignore[no-untyped-def]
    async def resolve(host: str, port: int) -> list[str]:
        return list(answers)

    return resolve


PUBLIC = "93.184.216.34"


async def test_public_https_host_is_pinned_to_the_resolved_ip() -> None:
    target = await resolve_outbound(
        "https://bridge.example.org/simplefin?x=1", resolver=_resolver(PUBLIC)
    )
    assert target.ip == PUBLIC
    args = target.httpx_request_args()
    assert args["url"] == f"https://{PUBLIC}:443/simplefin?x=1"
    assert args["headers"] == {"Host": "bridge.example.org"}
    assert args["extensions"] == {"sni_hostname": "bridge.example.org"}
    # httpx accepts the pinned request without a network call.
    request = httpx.Request(
        "GET", args["url"], headers=args["headers"], extensions=args["extensions"]
    )
    assert request.url.host == PUBLIC


async def test_credentials_in_url_never_appear_in_errors_or_repr() -> None:
    url = "https://user:hunter2canary@bridge.example.org/"
    target = await resolve_outbound(url, resolver=_resolver(PUBLIC))
    assert "hunter2canary" not in repr(target)
    with pytest.raises(OutboundRejected) as excinfo:
        await resolve_outbound("http://user:hunter2canary@bridge.example.org/")
    assert "hunter2canary" not in str(excinfo.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://bridge.example.org/",
        "ftp://bridge.example.org/",
        "file:///etc/passwd",
        "gopher://bridge.example.org/",
        "//bridge.example.org/",
        "https:///nohost",
        "",
        "https://bridge.example.org/ x",
        "https://bridge.example.org/\r\nHost: evil",
        "https://bridge.example.org:99999/",
    ],
)
async def test_bad_scheme_or_shape_is_rejected(url: str) -> None:
    with pytest.raises(OutboundRejected):
        await resolve_outbound(url, resolver=_resolver(PUBLIC))


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.255.255.254",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",  # noqa: S104
        "100.64.0.1",
        "224.0.0.1",
        "239.255.255.250",
        "240.0.0.1",
        "255.255.255.255",
        "192.0.2.1",
        "198.18.0.1",
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00::1",
        "ff02::1",
        "2001:db8::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:c0a8:0101",
        "64:ff9b::7f00:1",
        "2002:7f00:1::1",
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    ],
)
async def test_blocked_addresses_as_literals_and_as_dns_answers(ip: str) -> None:
    assert blocked_reason(ipaddress.ip_address(ip)) is not None
    host = f"[{ip}]" if ":" in ip else ip
    with pytest.raises(OutboundRejected):
        await resolve_outbound(f"https://{host}/", resolver=_resolver(PUBLIC))
    with pytest.raises(OutboundRejected):
        await resolve_outbound("https://rebind.example.org/", resolver=_resolver(ip))


async def test_one_bad_answer_among_good_ones_rejects_the_host() -> None:
    with pytest.raises(OutboundRejected):
        await resolve_outbound(
            "https://mixed.example.org/", resolver=_resolver(PUBLIC, "169.254.169.254")
        )


@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "0177.0.0.1", "127.1"])
async def test_alternate_ipv4_spellings_are_caught_after_resolution(host: str) -> None:
    # getaddrinfo expands these to 127.0.0.1; the validator checks what resolution returns.
    with pytest.raises(OutboundRejected):
        await resolve_outbound(f"https://{host}/", resolver=_resolver("127.0.0.1"))


async def test_empty_or_garbage_resolution_is_rejected() -> None:
    with pytest.raises(OutboundRejected):
        await resolve_outbound("https://x.example.org/", resolver=_resolver())
    with pytest.raises(OutboundRejected):
        await resolve_outbound("https://x.example.org/", resolver=_resolver("not-an-ip"))


def test_public_addresses_are_allowed() -> None:
    for ip in (PUBLIC, "8.8.8.8", "2606:4700:4700::1111"):
        assert blocked_reason(ipaddress.ip_address(ip)) is None


async def test_ipv6_target_is_bracketed_in_the_pinned_url() -> None:
    target = await resolve_outbound(
        "https://v6.example.org:8443/p", resolver=_resolver("2606:4700:4700::1111")
    )
    args = target.httpx_request_args()
    assert args["url"] == "https://[2606:4700:4700::1111]:8443/p"
    assert args["headers"] == {"Host": "v6.example.org:8443"}
