"""The conftest network guard blocks non-local egress."""

from __future__ import annotations

import socket

import pytest

from tests.conftest import NetworkBlockedError


def test_blocks_external_ip() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.connect(("203.0.113.10", 443))  # TEST-NET-3, never routed
    finally:
        sock.close()


def test_blocks_external_dns() -> None:
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("example.com", 443)


def test_allows_loopback() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(server.getsockname())
    finally:
        client.close()
        server.close()
