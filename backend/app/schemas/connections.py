"""Plaid Link and connection schemas (``/api/connections``). No secret appears in any output
except the Link token the browser needs to open Link, once, in the link-token response."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr

from app.schemas.common import Model

LoginKindName = Literal["bank_card", "retirement_hsa", "brokerage"]
LinkModeName = Literal["new", "update"]
SlotState = Literal["reserved", "linked", "abandoned", "unknown"]
EnvName = Literal["sandbox", "production"]


class LinkPlanIn(Model):
    login_kind: LoginKindName
    mode: LinkModeName = "new"
    connection_id: uuid.UUID | None = None


class ExistingConnectionOut(Model):
    id: uuid.UUID
    provider: str
    env: EnvName
    status: str
    created_at: datetime


class LinkPlanOut(Model):
    products: list[str]
    optional_products: list[str]
    days_requested: int | None
    ledger_count: int
    count_source: str
    existing_connections: list[ExistingConnectionOut]
    # The phrase for taking the next slot from the ledger count. None where no phrase applies
    # (sandbox, update mode). The link-token phrase uses max(ledger, owner count).
    phrase: str | None


class LinkTokenIn(Model):
    login_kind: LoginKindName
    mode: LinkModeName = "new"
    connection_id: uuid.UUID | None = None
    owner_count: int | None = Field(default=None, ge=0, le=10)
    not_a_duplicate: bool = False
    confirm_phrase: str | None = Field(default=None, max_length=200)


class LinkTokenOut(Model):
    link_token: str
    expiration: str | None
    # None for update mode: a relink reserves nothing.
    reservation_id: uuid.UUID | None


class ExchangeIn(Model):
    public_token: SecretStr = Field(min_length=1, max_length=512)
    reservation_id: uuid.UUID


class AbandonIn(Model):
    confirm_phrase: str = Field(max_length=200)


class SlotOut(Model):
    reservation_id: uuid.UUID
    state: SlotState
    flag: str | None
    connection_id: uuid.UUID | None


class ConnectionAccountOut(Model):
    id: uuid.UUID
    name: str
    mask: str | None
    type: str


class ConnectionOut(Model):
    id: uuid.UUID
    provider: str
    env: EnvName
    status: str
    created_at: datetime
    last_synced_at: datetime | None
    pending_accounts: int
    mapped_accounts: int
    ignored_accounts: int
    # Counted second Item from a repeated public token (ADR 0034 section 3), else None.
    slot_flag: str | None
    # Empty when every account of this connection is hidden from the reader.
    accounts: list[ConnectionAccountOut]


class OpenReservationOut(Model):
    id: uuid.UUID
    state: SlotState
    env: EnvName
    created_at: datetime


class ConnectionsOut(Model):
    connections: list[ConnectionOut]
    # Owner only: reservations still open or unresolved.
    open_reservations: list[OpenReservationOut]


class ExchangeOut(Model):
    connection: ConnectionOut
