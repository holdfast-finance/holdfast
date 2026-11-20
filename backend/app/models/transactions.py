"""Transactions, splits, categories and categorization rules."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    CATEGORY_KINDS,
    CATEGORY_SOURCES,
    CREATED_BY,
    SHA256_HEX_PATTERN,
    TRANSACTION_SOURCES,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    check_in,
    child_fk,
    parent_key,
)


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (
        check_in("kind", CATEGORY_KINDS),
        child_fk("parent_id", "category", ondelete="SET NULL"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    parent_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    name: Mapped[str]
    kind: Mapped[str]
    is_essential: Mapped[bool] = mapped_column(server_default=text("false"))
    default_tax_tag: Mapped[str | None]
    created_at: Mapped[CreatedAt]


class Rule(Base):
    """``match``: {merchant_regex, desc_regex, account_id, amount_min/max}.
    ``set``: {category_id, tax_tag, is_transfer, rename}."""

    __tablename__ = "rule"
    __table_args__ = (check_in("created_by", CREATED_BY),)

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    priority: Mapped[int] = mapped_column(server_default=text("100"))
    match: Mapped[dict[str, Any]]
    set_: Mapped[dict[str, Any]] = mapped_column("set")
    created_by: Mapped[str] = mapped_column(server_default=text("'user'"))
    hit_count: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[CreatedAt]


class Transaction(Base):
    """A transaction. ``amount``: outflow negative, inflow positive (Plaid is negated on ingest).

    Detail table: hidden from people who only have ``summary_only`` access to the account, and
    writable only by the account's owner, for joint accounts, or by household-wide jobs. Moving a
    row to another account (``account_id``) needs write access to both.

    **Transfers.** ``transfer_group_id`` requires ``is_transfer`` (CHECK). A group is exactly two
    legs with opposite-sign amounts on different accounts of the same household; the sync and
    matching services enforce that in Phase 2 (one-legged transfers to an unlinked account are
    legitimate: ``is_transfer`` with no group).

    **Pending to posted.** ``pending_external_id`` is Plaid's ``pending_transaction_id`` on a
    posted transaction: the ``external_id`` of the pending row it replaces. Phase 2 sync uses it
    to retire the pending row instead of double-counting it.

    **Splits.** If a transaction has splits, they sum exactly to ``amount`` (deferred constraint
    trigger, checked at commit). Changing ``amount`` re-runs the check.

    **Imports** (ADR 0017). An imported row (``source = 'import'``) always has an
    ``import_fingerprint`` (64-char hex, ``app.domain.fingerprint``), unique per account, so a
    re-import inserts nothing. ``import_run_id`` records which run created it (imports only).
    Once set, that link cannot be changed or cleared (trigger ``transaction_import_run_guard``).
    A run can't be deleted while its rows exist (NO ACTION), so undoing an import deletes its
    rows first, under RLS.

    **Supersession** (PLAN 8.1 step 5, "Plaid wins"). ``superseded_by_id`` points at the row that
    replaces this one; superseded rows stay (so their fingerprints keep re-imports idempotent) but
    every metric and view must exclude them (``superseded_by_id IS NULL``). The database enforces:
    same account (composite FK to ``(id, account_id)``), not self, one-to-one (a winner replaces
    at most one row), and no chains (a winner is live, and a row that replaces another can't
    itself be superseded). ``transaction_supersession_guard`` locks the winner, and any row this
    one already replaces, before that check. Deleting the winner (Plaid
    ``removed``) clears the link, so the replaced row counts again.
    """

    __tablename__ = "transaction"
    __table_args__ = (
        check_in("source", TRANSACTION_SOURCES),
        check_in("category_source", CATEGORY_SOURCES, nullable=True),
        child_fk("account_id", "account"),
        child_fk("category_id", "category", ondelete="SET NULL"),
        child_fk("receipt_document_id", "document", ondelete="SET NULL"),
        # NO ACTION: an import run can't be deleted while it still has rows (undo deletes them
        # first, under RLS). Household and account deletes still cascade both.
        child_fk("import_run_id", "import_run", ondelete="NO ACTION"),
        # Supersession stays within one account (and so one household).
        ForeignKeyConstraint(
            ["superseded_by_id", "account_id"],
            ["transaction.id", "transaction.account_id"],
            ondelete="SET NULL (superseded_by_id)",
        ),
        CheckConstraint("transfer_group_id IS NULL OR is_transfer", name="transfer_group"),
        CheckConstraint(
            "superseded_by_id IS NULL OR superseded_by_id <> id", name="not_self_superseded"
        ),
        CheckConstraint("import_run_id IS NULL OR source = 'import'", name="import_run_source"),
        CheckConstraint(
            "source <> 'import' OR import_fingerprint IS NOT NULL", name="import_fingerprinted"
        ),
        CheckConstraint(
            f"import_fingerprint IS NULL OR import_fingerprint ~ '{SHA256_HEX_PATTERN}'",
            name="import_fingerprint_hex",
        ),
        # Scoped per account (not global) so a duplicate can't reveal another household's data.
        UniqueConstraint("account_id", "external_id"),
        UniqueConstraint("account_id", "import_fingerprint"),
        # Target of the supersession FK.
        UniqueConstraint("id", "account_id"),
        # One-to-one: a winner replaces at most one row. Also serves the supersession FK.
        Index(
            "uq_transaction_superseded_by_id",
            "superseded_by_id",
            unique=True,
            postgresql_where=text("superseded_by_id IS NOT NULL"),
        ),
        Index("ix_transaction_account_id_posted_date", "account_id", "posted_date"),
        Index("ix_transaction_category_id_posted_date", "category_id", "posted_date"),
        Index("ix_transaction_merchant_name", "merchant_name"),
        Index(
            "ix_transaction_description_raw_trgm",
            "description_raw",
            postgresql_using="gin",
            postgresql_ops={"description_raw": "gin_trgm_ops"},
        ),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    account_id: Mapped[uuid.UUID]
    # Plaid transaction_id / SimpleFIN transaction id.
    external_id: Mapped[str | None]
    import_fingerprint: Mapped[str | None]
    posted_date: Mapped[date]
    authorized_date: Mapped[date | None]
    amount: Mapped[Money] = mapped_column(
        comment="Outflow negative, inflow positive. Plaid amounts are negated on ingest."
    )
    description_raw: Mapped[str]
    merchant_name: Mapped[str | None]
    plaid_pfc_primary: Mapped[str | None]
    plaid_pfc_detailed: Mapped[str | None]
    category_id: Mapped[uuid.UUID | None]
    category_source: Mapped[str | None]
    is_pending: Mapped[bool] = mapped_column(server_default=text("false"))
    is_transfer: Mapped[bool] = mapped_column(server_default=text("false"))
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    is_tax_relevant: Mapped[bool] = mapped_column(server_default=text("false"))
    tax_tag: Mapped[str | None]
    notes: Mapped[str | None]
    receipt_document_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    pending_external_id: Mapped[str | None] = mapped_column(
        index=True,
        comment="Plaid pending_transaction_id: external_id of the pending row replaced.",
    )
    source: Mapped[str]
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        comment="Row that replaces this one (Plaid wins). Superseded rows are excluded."
    )
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class TransactionSplit(Base):
    """One part of a split transaction.

    Invariant (deferred constraint trigger ``transaction_split_balance``, checked at commit):
    a transaction's splits sum **exactly** to its ``amount``, and every split has the same sign
    as the parent (no zero or opposite-sign parts). So write all of a transaction's splits in one
    database transaction. Visibility and write access follow the parent transaction.
    """

    __tablename__ = "transaction_split"
    __table_args__ = (
        child_fk("transaction_id", "transaction"),
        child_fk("category_id", "category", ondelete="SET NULL"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    transaction_id: Mapped[uuid.UUID] = mapped_column(index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    amount: Mapped[Money] = mapped_column(
        comment="Same sign as the parent; a transaction's splits sum to its amount."
    )
    tax_tag: Mapped[str | None]
    note: Mapped[str | None]
