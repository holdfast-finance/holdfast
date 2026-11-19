"""Transactions, splits, categories and categorization rules."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    CATEGORY_KINDS,
    CATEGORY_SOURCES,
    CREATED_BY,
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

    Detail table: hidden from people who only have ``summary_only`` access to the account.
    """

    __tablename__ = "transaction"
    __table_args__ = (
        check_in("source", TRANSACTION_SOURCES),
        check_in("category_source", CATEGORY_SOURCES, nullable=True),
        child_fk("account_id", "account"),
        child_fk("category_id", "category", ondelete="SET NULL"),
        child_fk("receipt_document_id", "document", ondelete="SET NULL"),
        # Scoped per account (not global) so a duplicate can't reveal another household's data.
        UniqueConstraint("account_id", "external_id"),
        UniqueConstraint("account_id", "import_fingerprint"),
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
    amount: Mapped[Money]
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
    source: Mapped[str]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class TransactionSplit(Base):
    __tablename__ = "transaction_split"
    __table_args__ = (
        child_fk("transaction_id", "transaction"),
        child_fk("category_id", "category", ondelete="SET NULL"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    transaction_id: Mapped[uuid.UUID] = mapped_column(index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    amount: Mapped[Money]
    tax_tag: Mapped[str | None]
    note: Mapped[str | None]
