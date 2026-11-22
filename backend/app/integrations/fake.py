"""In-memory connector for synthetic contract tests."""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import ClassVar

from app.domain.connector import SyncBatch
from app.integrations.base import (
    ConnectionRef,
    ConnectionState,
    ConnectorError,
    ConnectorHealth,
    LinkContext,
    LinkedConnection,
    LinkStart,
)
from app.security.secret_token import SecretToken


class FakeConnector:
    provider: ClassVar[str] = "fake"

    def __init__(self, pages: Mapping[str | None, SyncBatch | ConnectorError]) -> None:
        self._pages = dict(pages)

    async def begin_link(self, ctx: LinkContext) -> LinkStart:
        return LinkStart(SecretToken("synthetic-link"))

    async def complete_link(self, ctx: LinkContext, proof: SecretToken) -> LinkedConnection:
        return LinkedConnection("synthetic-connection", SecretToken("synthetic-credential"))

    async def begin_relink(self, ctx: LinkContext, conn: ConnectionRef) -> LinkStart:
        return LinkStart(SecretToken("synthetic-relink"))

    async def fetch(
        self, conn: ConnectionRef, credential: SecretToken, cursor: str | None
    ) -> SyncBatch:
        """Replay the same outcome for the same cursor, including after a retry."""
        page = self._pages.get(cursor)
        if page is None:
            raise ConnectorError("invalid_cursor", "UNSCRIPTED_CURSOR", None)
        if isinstance(page, ConnectorError):
            raise ConnectorError(page.kind, page.code, page.request_id)
        return page

    def health(self, state: ConnectionState, now: datetime) -> ConnectorHealth:
        last_success = state.last_success_at
        stale = last_success is None or now - last_success > timedelta(hours=48)
        return ConnectorHealth(state, stale)
