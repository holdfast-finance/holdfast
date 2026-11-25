"""Test-only registry helpers. Production code must not swap providers."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.integrations.base import BankConnector
from app.integrations.registry import ConnectorRegistry


@contextmanager
def override(target: ConnectorRegistry, connector: type[BankConnector]) -> Iterator[None]:
    """Temporarily replace or add a provider, bypassing the freeze."""
    provider = connector.provider
    previous = target._connectors.get(provider)
    target._connectors[provider] = connector
    try:
        yield
    finally:
        if previous is None:
            del target._connectors[provider]
        else:
            target._connectors[provider] = previous
