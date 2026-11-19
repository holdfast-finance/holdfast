"""Institutions, bank connections (Plaid / SimpleFIN), accounts and balance snapshots."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    ACCOUNT_SOURCES,
    ACCOUNT_TYPES,
    ACCOUNT_VISIBILITIES,
    BANK_CONNECTION_STATUSES,
    BANK_ENVS,
    BANK_PROVIDERS,
    TAX_TREATMENTS,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    check_in,
    check_masked,
    child_fk,
    parent_key,
)


class Institution(Base):
    """Shared reference data (no household_id, no RLS). The app may not delete rows."""

    __tablename__ = "institution"

    id: Mapped[UUIDPK]
    plaid_institution_id: Mapped[str | None] = mapped_column(unique=True)
    name: Mapped[str]
    logo_url: Mapped[str | None]
    created_at: Mapped[CreatedAt]


class BankConnection(Base):
    """A linked Plaid Item or SimpleFIN connection (replaces PLAN 5 ``plaid_item``).

    ``access_token_enc`` is Fernet ciphertext. Never log or return it.
    """

    __tablename__ = "bank_connection"
    __table_args__ = (
        check_in("provider", BANK_PROVIDERS),
        check_in("env", BANK_ENVS),
        check_in("status", BANK_CONNECTION_STATUSES),
        UniqueConstraint("provider", "external_id"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    institution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("institution.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str]
    external_id: Mapped[str]
    access_token_enc: Mapped[bytes]
    env: Mapped[str] = mapped_column(server_default=text("'sandbox'"))
    products: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    error_code: Mapped[str | None]
    consent_expires_at: Mapped[datetime | None]
    tx_cursor: Mapped[str | None]
    last_synced_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_error_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Account(Base):
    """An account. ``owner_person_id`` NULL means joint. ``visibility`` is enforced by RLS.

    ``mask`` holds the last few digits only. Full account numbers are never stored.
    """

    __tablename__ = "account"
    __table_args__ = (
        check_in("source", ACCOUNT_SOURCES),
        check_in("type", ACCOUNT_TYPES),
        check_in("tax_treatment", TAX_TREATMENTS),
        check_in("visibility", ACCOUNT_VISIBILITIES),
        check_masked("mask"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency"),
        child_fk("bank_connection_id", "bank_connection", ondelete="SET NULL"),
        # NO ACTION: a person who still owns accounts can't be deleted (reassign first), so a
        # private account never silently turns joint. Household deletes still cascade.
        child_fk("owner_person_id", "person", ondelete="NO ACTION"),
        UniqueConstraint("bank_connection_id", "external_account_id"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    bank_connection_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    # Plaid account_id / SimpleFIN account id, unique per connection.
    external_account_id: Mapped[str | None]
    source: Mapped[str]
    name: Mapped[str]
    official_name: Mapped[str | None]
    mask: Mapped[str | None]
    type: Mapped[str]
    subtype: Mapped[str | None]
    tax_treatment: Mapped[str] = mapped_column(server_default=text("'taxable'"))
    currency: Mapped[str] = mapped_column(server_default=text("'USD'"))
    is_hidden: Mapped[bool] = mapped_column(server_default=text("false"))
    visibility: Mapped[str] = mapped_column(server_default=text("'shared'"))
    opened_at: Mapped[date | None]
    closed_at: Mapped[date | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class BalanceSnapshot(Base):
    """Daily balance per account. Visible to anyone who can see the account (summary_only too)."""

    __tablename__ = "balance_snapshot"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            ondelete="CASCADE",
        ),
        Index("ix_balance_snapshot_as_of_date", "as_of_date"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(primary_key=True)
    household_id: Mapped[HouseholdId]
    current: Mapped[Money | None]
    available: Mapped[Money | None]
    limit_amt: Mapped[Money | None]
    created_at: Mapped[CreatedAt]
