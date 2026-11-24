"""Shared fixtures and the outbound network guard.

Service-backed tests read ``TEST_DATABASE_URL`` (the runtime app role),
``TEST_MIGRATIONS_DATABASE_URL`` (the schema owner role) and ``TEST_REDIS_URL`` (falling back to
``REDIS_URL``). Locally they skip with a reason when the services are unreachable. When ``CI``
is set, an unreachable service is a hard failure instead, so these tests always run in CI.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import pytest

from app.config import Settings

# Every env var from the Phase 0 contract, cleared for tests that check defaults.
CONTRACT_ENV_VARS = (
    "APP_ENV",
    "APP_DEPLOYMENT",
    "APP_BASE_URL",
    "APP_HOST",
    "CADDY_BIND",
    "TAILSCALE_BIND_IP",
    "CF_API_TOKEN",
    "SESSION_SECRET",
    "FERNET_KEY",
    "DATABASE_URL",
    "MIGRATIONS_DATABASE_URL",
    "POSTGRES_PASSWORD",
    "POSTGRES_APP_PASSWORD",
    "POSTGRES_OWNER_PASSWORD",
    "REDIS_URL",
    "REDIS_PASSWORD",
    "DOCUMENT_STORE",
    "PLAID_ENV",
    "HOLDFAST_SANDBOX",
    "HOLDFAST_INSTANCE",
    "PLAID_CLIENT_ID",
    "PLAID_SECRET_SANDBOX",
    "PLAID_SECRET_PRODUCTION",
    "PLAID_PRODUCTS",
    "PLAID_COUNTRY_CODES",
    "PLAID_REDIRECT_URI",
    "PLAID_WEBHOOK_URL",
    "PLAID_TX_DAYS_REQUESTED",
    "SIMPLEFIN_ACCESS_URL",
    "AI_PROVIDER",
    "ANTHROPIC_API_KEY",
    "AGENT_MODEL_REVIEW",
    "AGENT_MODEL_CHAT",
    "AGENT_MONTHLY_TOKEN_BUDGET",
    "OLLAMA_BASE_URL",
    "OPENAI_COMPAT_BASE_URL",
    "OPENAI_COMPAT_API_KEY",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASS",
    "ALERT_EMAIL",
    "NTFY_URL",
    "NTFY_TOPIC",
    "HOUSEHOLD_STATE",
    "HISTORY_START",
    "FEATURE_TAX",
    "FEATURE_CRYPTO_SPEND",
    "FEATURE_PUBLIC_SECTOR",
    "WEB_UPSTREAM",
)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def env_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


def env_migrations_database_url() -> str | None:
    return os.environ.get("TEST_MIGRATIONS_DATABASE_URL")


def env_redis_url() -> str | None:
    return os.environ.get("TEST_REDIS_URL") or os.environ.get("REDIS_URL")


# --------------------------------------------------------------------------- network guard


class NetworkBlockedError(RuntimeError):
    pass


def _allowed_hosts() -> set[str]:
    hosts = set(LOCAL_HOSTS)
    for url in (env_database_url(), env_migrations_database_url(), env_redis_url()):
        if url and (host := urlsplit(url).hostname):
            hosts.add(host)
    return hosts


def _resolve(hosts: set[str]) -> set[str]:
    ips: set[str] = set()
    for host in hosts:
        try:
            for info in socket.getaddrinfo(host, None):
                ips.add(str(info[4][0]))
        except OSError:
            continue
    return ips


def _is_allowed_ip(host: str, allowed_ips: set[str]) -> bool:
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return ip.is_loopback or str(ip) in allowed_ips


@pytest.fixture(autouse=True, scope="session")
def network_guard() -> Iterator[None]:
    """Block outbound sockets except to localhost and the configured DB/Redis hosts."""
    allowed_hosts = _allowed_hosts()
    allowed_ips = _resolve(allowed_hosts)
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def check(sock: socket.socket, address: Any) -> None:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return  # unix sockets, socketpairs
        host = str(address[0])
        if host in allowed_hosts or _is_allowed_ip(host, allowed_ips):
            return
        raise NetworkBlockedError(f"outbound network blocked in tests: {host}")

    def guarded_connect(self: socket.socket, address: Any) -> None:
        check(self, address)
        real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        check(self, address)
        return real_connect_ex(self, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None:
            name = host.decode() if isinstance(host, bytes) else str(host)
            if name not in allowed_hosts and not _is_ip_literal(name):
                raise NetworkBlockedError(f"DNS lookup blocked in tests: {name}")
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign,assignment]
    socket.getaddrinfo = guarded_getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.getaddrinfo = real_getaddrinfo


def _is_ip_literal(name: str) -> bool:
    try:
        ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- settings


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove every contract env var so Settings sees only its own defaults."""
    for name in CONTRACT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_settings(**overrides: Any) -> Settings:
    """Settings that never read a developer's real `.env` file."""
    return Settings(_env_file=None, **overrides)


# --------------------------------------------------------------------------- services


def _unavailable(reason: str) -> None:
    if os.environ.get("CI"):
        pytest.fail(f"{reason} (required in CI)")
    pytest.skip(reason)


def _reachable(url: str, default_port: int) -> bool:
    parts = urlsplit(url)
    host, port = parts.hostname or "localhost", parts.port or default_port
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


@pytest.fixture
def database_url() -> str:
    url = env_database_url()
    if not url:
        _unavailable("TEST_DATABASE_URL is not set; start Postgres to run DB tests")
    assert url is not None
    if not _reachable(url, 5432):
        _unavailable("Postgres at TEST_DATABASE_URL is unreachable")
    return url


@pytest.fixture
def migrations_database_url() -> str:
    url = env_migrations_database_url()
    if not url:
        _unavailable("TEST_MIGRATIONS_DATABASE_URL is not set; it names the schema owner role")
    assert url is not None
    if not _reachable(url, 5432):
        _unavailable("Postgres at TEST_MIGRATIONS_DATABASE_URL is unreachable")
    return url


@pytest.fixture
def redis_url() -> str:
    url = env_redis_url()
    if not url:
        _unavailable("TEST_REDIS_URL / REDIS_URL is not set; start Redis to run these tests")
    assert url is not None
    if not _reachable(url, 6379):
        _unavailable("Redis at TEST_REDIS_URL / REDIS_URL is unreachable")
    return url
