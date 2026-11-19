"""Client IP for rate limiting and the audit log.

Requests reach the API through Caddy, so the TCP peer is the proxy. ``X-Forwarded-For`` is used
only when the peer is a trusted proxy (``TRUSTED_PROXIES``), and then the right-most address that
isn't itself a trusted proxy wins (everything to its left is client-controlled).
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=8)
def _networks(cidrs: tuple[str, ...]) -> tuple[IPNetwork, ...]:
    return tuple(ipaddress.ip_network(c, strict=False) for c in cidrs)


def _parse(value: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(value.strip().split("%", 1)[0])
    except ValueError:
        return None


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, trusted: tuple[str, ...]
) -> IPAddress | None:
    nets = _networks(trusted)
    peer_ip = _parse(peer) if peer else None

    def is_trusted(ip: IPAddress) -> bool:
        return any(ip in n for n in nets if n.version == ip.version)

    if peer_ip is None or not is_trusted(peer_ip) or not forwarded_for:
        return peer_ip
    for part in reversed(forwarded_for.split(",")):
        ip = _parse(part)
        if ip is None:
            return peer_ip
        if not is_trusted(ip):
            return ip
    return peer_ip


def client_ip(request: Request) -> IPAddress | None:
    if "client_ip" in request.scope.get("state", {}):
        cached: IPAddress | None = request.state.client_ip
        return cached
    trusted = tuple(request.app.state.settings.trusted_proxies)
    peer = request.client.host if request.client else None
    ip = resolve_client_ip(peer, request.headers.get("x-forwarded-for"), trusted)
    request.state.client_ip = ip
    return ip
