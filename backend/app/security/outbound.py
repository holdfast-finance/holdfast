"""Outbound URL validation for server-side fetches of household-supplied URLs (SSRF guard).

MANDATORY for every connector that requests a URL a household typed in (hosted mode:
``simplefin_access_url``, an OpenAI-compatible base URL; ADR 0015). Use it like this::

    target = await resolve_outbound(url)              # raises OutboundRejected
    kwargs = target.httpx_request_args()
    await client.get(**kwargs, follow_redirects=False)  # never follow redirects unvalidated

Rules: ``https`` only; the host is resolved once and *every* resolved address must be globally
routable (private, loopback, link-local, multicast, reserved, unspecified, carrier-grade NAT,
IPv4-mapped / NAT64 / 6to4 / Teredo IPv6 forms are all refused); the request is then pinned to the
validated IP, so a second DNS answer (rebinding) cannot redirect it. The TLS name check and SNI
still use the original hostname. Errors carry a short reason code and never echo the URL, which
may hold credentials (a SimpleFIN access URL does).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], Awaitable[list[str]]]

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")


class OutboundRejected(Exception):
    """The URL must not be fetched. ``reason`` is a fixed code; the URL is never included."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"outbound URL rejected: {reason}")


def blocked_reason(ip: IPAddress) -> str | None:
    """Why ``ip`` is not allowed as an outbound target, or None when it is globally routable."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return blocked_reason(ip.ipv4_mapped) or "ipv4_mapped"
        if ip in _NAT64 or ip in _NAT64_LOCAL or ip in _SIX_TO_FOUR or ip in _TEREDO:
            return "embedded_ipv4"
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_private:
        return "private"
    if ip.is_reserved:
        return "reserved"
    if not ip.is_global:  # carrier-grade NAT, benchmarking, documentation ranges, ...
        return "not_global"
    return None


@dataclass(frozen=True)
class OutboundTarget:
    """A validated URL pinned to one resolved address."""

    url: str = field(repr=False)  # original URL, may hold credentials: never log
    hostname: str
    port: int
    ip: str

    def httpx_request_args(self) -> dict[str, Any]:
        """``url``/``headers``/``extensions`` for ``httpx`` that connect to the pinned IP.

        The Host header and the TLS SNI/certificate name stay the original hostname.
        """
        parts = urlsplit(self.url)
        host = f"[{self.ip}]" if ":" in self.ip else self.ip
        netloc = f"{host}:{self.port}"
        pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
        host_header = self.hostname if self.port == 443 else f"{self.hostname}:{self.port}"
        return {
            "url": pinned,
            "headers": {"Host": host_header},
            "extensions": {"sni_hostname": self.hostname},
        }


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise OutboundRejected("unresolvable") from None
    return [str(info[4][0]).split("%", 1)[0] for info in infos]


async def resolve_outbound(url: str, *, resolver: Resolver | None = None) -> OutboundTarget:
    """Validate ``url`` and resolve it to a pinned target. Raises :class:`OutboundRejected`."""
    if not url or any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise OutboundRejected("malformed")
    try:
        parts = urlsplit(url)
        port = parts.port or 443
        hostname = parts.hostname
    except ValueError:
        raise OutboundRejected("malformed") from None
    if parts.scheme.lower() != "https":
        raise OutboundRejected("scheme")
    if not hostname:
        raise OutboundRejected("no_host")
    if not 0 < port <= 65535:
        raise OutboundRejected("port")

    candidates: list[str]
    try:
        literal = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError:
        candidates = await (resolver or _system_resolver)(hostname.rstrip("."), port)
    else:
        candidates = [str(literal)]
    if not candidates:
        raise OutboundRejected("unresolvable")

    addresses: list[IPAddress] = []
    for raw in candidates:
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError:
            raise OutboundRejected("unresolvable") from None
    for address in addresses:
        if blocked_reason(address) is not None:
            raise OutboundRejected("blocked_address")  # one bad answer poisons the whole set
    return OutboundTarget(url=url, hostname=hostname, port=port, ip=str(addresses[0]))
