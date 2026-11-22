"""Read-only bank connector boundary with opaque link and connection credentials."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol, get_args, runtime_checkable
from uuid import UUID

from app.domain.connector import SyncBatch
from app.security.secret_token import SecretToken

# P5a note: this boundary is read-only by construction, and the structural allowlist test in
# tests/integrations pins the public surface to the protocol. The Plaid HTTP client (P5a) must
# also carry an endpoint-path allowlist that refuses /transfer/*, /payment_initiation/* and
# /signal/*, so no code path can reach money movement even through a new method.

ErrorKind = Literal[
    "retryable",
    "rate_limited",
    "auth_required",
    "institution_down",
    "invalid_cursor",
    "invalid_request",
    "permanent",
]
ERROR_KINDS: frozenset[str] = frozenset(get_args(ErrorKind))
_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _check_code(code: str) -> None:
    if not _CODE.fullmatch(code):
        raise ValueError("code must match ^[A-Z0-9_]{1,64}$")


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

    def __post_init__(self) -> None:
        if self.error_code is not None:
            _check_code(self.error_code)


@dataclass(frozen=True)
class ConnectorHealth:
    state: ConnectionState
    stale: bool


class ConnectorError(Exception):
    """Carries a classified kind, a safe code and an optional provider request ID."""

    def __init__(self, kind: str, code: str, request_id: str | None) -> None:
        if kind not in ERROR_KINDS:
            raise ValueError("unknown connector error kind")
        _check_code(code)
        if request_id is not None and not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("request_id must match ^[A-Za-z0-9-]{1,64}$")
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
