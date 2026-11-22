"""Explicit registration of connector classes by provider."""

from collections.abc import Iterator
from contextlib import contextmanager

from app.integrations.base import BankConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, type[BankConnector]] = {}

    def register(self, connector: type[BankConnector]) -> None:
        provider = connector.provider
        if provider in self._connectors:
            raise ValueError(f"connector {provider!r} is already registered")
        self._connectors[provider] = connector

    def get(self, provider: str) -> type[BankConnector]:
        return self._connectors[provider]

    def __iter__(self) -> Iterator[type[BankConnector]]:
        return iter(self._connectors.values())

    @contextmanager
    def override(self, connector: type[BankConnector]) -> Iterator[None]:
        """Temporarily replace or add a provider in tests."""
        provider = connector.provider
        previous = self._connectors.get(provider)
        self._connectors[provider] = connector
        try:
            yield
        finally:
            if previous is None:
                del self._connectors[provider]
            else:
                self._connectors[provider] = previous


# Register production adapters here as they are implemented.
registry = ConnectorRegistry()
