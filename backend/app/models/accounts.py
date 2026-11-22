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
    CLIENT_FP_PATTERN,
    TAX_TREATMENTS,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    check_in,
    check_masked,
    check_non_negative,
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

    ``client_fp`` (Plaid only) is the fingerprint of the Plaid client id that created the Item
    (ADR 0034): Items belong to their client, so a changed client id is detected at use time.
    ``pending_setup``: linked, but nothing is ingested until the owner maps its accounts.
    """

    __tablename__ = "bank_connection"
    __table_args__ = (
        check_in("provider", BANK_PROVIDERS),
        check_in("env", BANK_ENVS),
        check_in("status", BANK_CONNECTION_STATUSES),
        CheckConstraint(
            f"client_fp IS NULL OR client_fp ~ '{CLIENT_FP_PATTERN}'", name="client_fp_format"
        ),
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
    client_fp: Mapped[str | None]
    last_synced_at: Mapped[datetime | None]
    last_success_at: Mapped[datetime | None]
    last_error_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Account(Base):
    """An account. ``owner_person_id`` NULL means joint. ``visibility`` is enforced by RLS.

    Reading follows ``visibility`` (PLAN 20.3). Writing is narrower (ADR 0008): only the owner,
    anyone for a joint account, or a household-wide job may insert, update or delete it, and a
    trigger rejects changes to ``visibility`` / ``owner_person_id`` by anyone else. A shared or
    summary_only account is read-only to the other people in the household.

    ``mask`` holds at most four digits. Full account numbers are never stored.

    **Statement key and adoption** (ADR 0033). ``statement_key`` is the statement importer's key
    (profile, last four digits, block hash), unique per household and fixed once set. Statement
    import finds its account by this key whatever the account's ``source``, so a statement
    re-imported after a connector adopted the account lands on the same row. Adoption turns an
    ``import`` account into a ``plaid`` / ``simplefin`` one in place and stamps ``adopted_at``;
    the ``account_guard_source`` trigger allows exactly that change and its reverse (un-adopt).
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
        # Imported accounts have no connection, so the key above never fires for them. The
        # statement key stays with the account through adoption.
        UniqueConstraint("household_id", "statement_key"),
        CheckConstraint(
            "adopted_at IS NULL OR (statement_key IS NOT NULL"
            " AND source IN ('plaid', 'simplefin'))",
            name="adopted_is_connected",
        ),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    bank_connection_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    # Plaid account_id / SimpleFIN account id, unique per connection.
    external_account_id: Mapped[str | None]
    # The statement importer's key; NULL for accounts no statement created.
    statement_key: Mapped[str | None]
    # When a connector adopted this imported account; NULL otherwise (and after un-adopt).
    adopted_at: Mapped[datetime | None]
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
    """Daily balance per account. Visible to anyone who can see the account (summary_only too).

    Sign convention (as an asset from the household's point of view):

    - ``current``: positive for assets (depository, investment); **negative = owed** for credit
      and loan accounts. Plaid reports liabilities as positive, so negate on ingest.
    - ``available``: what can be spent or drawn right now, as reported (positive; for a card
      that's remaining credit). NULL when the institution doesn't say.
    - ``limit_amt``: the credit limit as a positive magnitude (CHECK ``>= 0``).

    Written only by the account's owner, for joint accounts, or by household-wide jobs.

    ``bank_connection_id`` is set on connector-written snapshots only (statement import writes
    none), so un-adopt deletes exactly the connector's snapshots (ADR 0033 §5). Deleting the
    connection clears it and keeps the history.
    """

    __tablename__ = "balance_snapshot"
    __table_args__ = (
        check_non_negative("limit_amt"),
        ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            ondelete="CASCADE",
        ),
        child_fk("bank_connection_id", "bank_connection", ondelete="SET NULL"),
        Index("ix_balance_snapshot_as_of_date", "as_of_date"),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    as_of_date: Mapped[date] = mapped_column(primary_key=True)
    household_id: Mapped[HouseholdId]
    # The connection that wrote this snapshot; NULL for manual and pre-Phase-2 rows.
    bank_connection_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    current: Mapped[Money | None] = mapped_column(
        comment="Assets positive; credit/loan negative = owed (Plaid negated on ingest)."
    )
    available: Mapped[Money | None] = mapped_column(
        comment="Spendable / drawable now, as reported (positive; remaining credit for cards)."
    )
    limit_amt: Mapped[Money | None] = mapped_column(comment="Credit limit, positive magnitude.")
    created_at: Mapped[CreatedAt]


class AccountCoverage(Base):
    """A date interval of an adopted account that statements already cover (ADR 0035 §1).

    Written in the adoption unit of work (one row per reconciled run, plus the runs of twins
    being closed; overlapping intervals merged), deleted by un-adopt. A posted connector row
    dated inside an interval (inclusive) is skipped, so coverage decides which connector rows
    reach the ledger.

    RLS follows the account's detail tables: reading needs the account's details to be visible
    (not summary_only for someone else), and writing needs the account to be writable (owner,
    joint) or a household-wide session. So nobody can read another person's coverage or add
    coverage that hides their connector rows.

    ``source_import_run_id`` is the run the interval came from, NULL for a merged interval or
    once that run is deleted (coverage is fixed at adoption, so the interval stays).
    """

    __tablename__ = "account_coverage"
    __table_args__ = (
        child_fk("account_id", "account"),
        child_fk("source_import_run_id", "import_run", ondelete="SET NULL"),
        CheckConstraint("start_date <= end_date", name="period_order"),
        Index("ix_account_coverage_account_id_start_date", "account_id", "start_date"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    account_id: Mapped[uuid.UUID]
    start_date: Mapped[date]
    end_date: Mapped[date]
    source_import_run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    created_at: Mapped[CreatedAt]
