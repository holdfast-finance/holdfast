"""Read-only bank connector boundary with opaque link and connection credentials."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol, runtime_checkable
from uuid import UUID

from app.domain.connector import SyncBatch
from app.security.secret_token import SecretToken


@dataclass(frozen=True)
class LinkContext:
    household_id: UUID
    user_id: UUID


@dataclass(frozen=True)
class LinkStart:
    token: SecretToken
    expires_at: datetime | None = None


@dataclass(frozen=True)
class LinkedConnection:
    external_id: str
    credential: SecretToken


@dataclass(frozen=True)
class ConnectionRef:
    id: UUID
    external_id: str


@dataclass(frozen=True)
class ConnectionState:
    status: str
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    error_code: str | None = None
    post_relink_duplicates_removed: int = 0


@dataclass(frozen=True)
class ConnectorHealth:
    state: ConnectionState
    stale: bool


class ConnectorError(Exception):
    """Carries a classified kind, a safe code and an optional provider request ID."""

    def __init__(self, kind: str, code: str, request_id: str | None) -> None:
        super().__init__("Bank connector operation failed")
        self.kind = kind
        self.code = code
        self.request_id = request_id


@runtime_checkable
class BankConnector(Protocol):
    """Links authorized data sources and reads normalized account and transaction data."""

    provider: ClassVar[str]

    async def begin_link(self, ctx: LinkContext) -> LinkStart: ...

    async def complete_link(self, ctx: LinkContext, proof: SecretToken) -> LinkedConnection: ...

    async def begin_relink(self, ctx: LinkContext, conn: ConnectionRef) -> LinkStart: ...

    async def fetch(
        self, conn: ConnectionRef, credential: SecretToken, cursor: str | None
    ) -> SyncBatch: ...

    def health(self, state: ConnectionState, now: datetime) -> ConnectorHealth: ...
