"""Explicit registration of connector classes by provider."""

from collections.abc import Iterator

from app.integrations.base import BankConnector


class ConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, type[BankConnector]] = {}
        self._frozen = False

    def freeze(self) -> None:
        """Close registration. Call once at app and worker startup; repeat calls are no-ops."""
        self._frozen = True

    def register(self, connector: type[BankConnector]) -> None:
        if self._frozen:
            raise RuntimeError("connector registry is frozen")
        provider = connector.provider
        if provider in self._connectors:
            raise ValueError(f"connector {provider!r} is already registered")
        self._connectors[provider] = connector

    def get(self, provider: str) -> type[BankConnector]:
        return self._connectors[provider]

    def __iter__(self) -> Iterator[type[BankConnector]]:
        return iter(self._connectors.values())


# Register production adapters here as they are implemented. Startup (app lifespan and worker)
# calls registry.freeze() afterwards. Tests swap providers with the helper in tests/integrations.
registry = ConnectorRegistry()
