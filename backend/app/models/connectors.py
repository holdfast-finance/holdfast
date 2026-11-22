"""Connector bookkeeping: per-account link decisions and the Plaid slot ledger (Phase 2).

ADR 0033 (``connector_account_link``) and ADR 0034 (``plaid_link_slot``). Triggers, the
DELETE refusal and ``plaid_slot_count()`` live in migration ``0011_connector_link``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, SmallInteger, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    BANK_ENVS,
    CLIENT_FP_PATTERN,
    CONNECTOR_LINK_STATES,
    PLAID_SLOT_STATES,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    check_code,
    check_in,
    child_fk,
)


class ConnectorAccountLink(Base):
    """The owner's decision for one account a connection reports (ADR 0033 §1).

    ``IngestService`` resolves accounts only through this table: an unseen
    ``external_account_id`` gets a ``pending`` row and nothing is ingested until the owner maps
    it to an account (adopted or new) or ignores it. ``status_code`` is the connector's
    per-account status (for example SimpleFIN ``con.auth``), a code, never a message.

    ``account_id`` has no ON DELETE action: an account a mapped link points at can't be deleted
    until the link is un-mapped. Deleting the connection deletes its links.
    ``decided_by_user_id`` has no FK, like ``import_run.reviewed_by_user_id``: deleting a user
    must not rewrite who decided. The audit log is the record.
    """

    __tablename__ = "connector_account_link"
    __table_args__ = (
        check_in("state", CONNECTOR_LINK_STATES),
        check_code("status_code"),
        CheckConstraint("(state = 'mapped') = (account_id IS NOT NULL)", name="mapped_has_account"),
        CheckConstraint("(state = 'pending') = (decided_at IS NULL)", name="decided_when_done"),
        child_fk("bank_connection_id", "bank_connection"),
        child_fk("account_id", "account", ondelete="NO ACTION"),
        UniqueConstraint("bank_connection_id", "external_account_id"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    bank_connection_id: Mapped[uuid.UUID]  # leads the unique key above, which serves the FK
    external_account_id: Mapped[str]
    state: Mapped[str] = mapped_column(server_default=text("'pending'"))
    account_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    decided_by_user_id: Mapped[uuid.UUID | None]
    decided_at: Mapped[datetime | None]
    status_code: Mapped[str | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PlaidLinkSlot(Base):
    """One production Item a Plaid client may have created: the slot ledger (ADR 0034 §1).

    Keyed by ``client_fp`` across households, because Plaid's 10 Trial Items are per client,
    ever. Rows are never deleted by the app (no DELETE grant; a trigger refuses DELETE outside a
    household cascade), and a deleted connection only clears ``bank_connection_id``.

    - ``state``: ``reserved`` at Link-token time, then ``linked``, ``abandoned`` or ``unknown``.
      ``reserved`` may move to any other state, ``unknown`` only to ``abandoned`` (trigger).
      Only ``abandoned`` stops counting (``plaid_slot_count()``).
    - One ``reserved`` row per ``(client_fp, env)`` (partial unique index), across households.
    - ``link_token_enc``: the sealed Link token, kept only while ``reserved`` (CHECK).
    - ``env`` comes from the server's ``PLAID_ENV``, never from a request. A production row
      carries the owner's own count of Items on the client (``owner_count``).
    - ``created_by_user_id`` has no FK: the ledger must outlive the user who linked.
    """

    __tablename__ = "plaid_link_slot"
    __table_args__ = (
        check_in("state", PLAID_SLOT_STATES),
        check_in("env", BANK_ENVS),
        check_code("flag"),
        CheckConstraint(f"client_fp ~ '{CLIENT_FP_PATTERN}'", name="client_fp_format"),
        CheckConstraint(
            "state = 'reserved' OR link_token_enc IS NULL", name="link_token_only_while_reserved"
        ),
        CheckConstraint(
            "owner_count IS NULL OR owner_count BETWEEN 0 AND 10", name="owner_count_range"
        ),
        CheckConstraint(
            "env <> 'production' OR owner_count IS NOT NULL", name="production_has_owner_count"
        ),
        CheckConstraint("state <> 'linked' OR item_id IS NOT NULL", name="linked_has_item"),
        CheckConstraint("(state = 'reserved') = (resolved_at IS NULL)", name="resolved_when_done"),
        child_fk("bank_connection_id", "bank_connection", ondelete="SET NULL"),
        Index(
            "uq_plaid_link_slot_client_fp_env_reserved",
            "client_fp",
            "env",
            unique=True,
            postgresql_where=text("state = 'reserved'"),
        ),
        # plaid_slot_count(): every row of one client and env.
        Index("ix_plaid_link_slot_client_fp_env", "client_fp", "env"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    client_fp: Mapped[str]
    env: Mapped[str]
    state: Mapped[str] = mapped_column(server_default=text("'reserved'"))
    link_token_enc: Mapped[bytes | None]
    owner_count: Mapped[int | None] = mapped_column(SmallInteger)
    item_id: Mapped[str | None] = mapped_column(unique=True)
    bank_connection_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    created_by_user_id: Mapped[uuid.UUID]
    created_at: Mapped[CreatedAt]
    resolved_at: Mapped[datetime | None]
    flag: Mapped[str | None]
