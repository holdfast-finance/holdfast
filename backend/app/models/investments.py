"""Securities, holdings, investment transactions and liabilities."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, ForeignKeyConstraint, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    INVESTMENT_TXN_TYPES,
    LIABILITY_KINDS,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    Price,
    Quantity,
    Rate,
    check_in,
    child_fk,
)


class Security(Base):
    """Shared reference data (no household_id, no RLS). The app may not delete rows."""

    __tablename__ = "security"

    id: Mapped[UUIDPK]
    plaid_security_id: Mapped[str | None] = mapped_column(unique=True)
    ticker: Mapped[str | None] = mapped_column(index=True)
    name: Mapped[str]
    type: Mapped[str | None]
    cusip: Mapped[str | None]
    created_at: Mapped[CreatedAt]


class Holding(Base):
    """Position per account, security and day. Detail table (hidden from summary_only)."""

    __tablename__ = "holding"
    __table_args__ = (
        child_fk("account_id", "account"),
        UniqueConstraint("account_id", "security_id", "as_of_date"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    account_id: Mapped[uuid.UUID]  # indexed by the unique constraint
    security_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("security.id", ondelete="RESTRICT"), index=True
    )
    as_of_date: Mapped[date]
    quantity: Mapped[Quantity]
    cost_basis: Mapped[Money | None]
    value: Mapped[Money | None]
    created_at: Mapped[CreatedAt]


class InvestmentTxn(Base):
    """Buys, sells, dividends, contributions, fees. Detail table (hidden from summary_only)."""

    __tablename__ = "investment_txn"
    __table_args__ = (
        check_in("type", INVESTMENT_TXN_TYPES),
        child_fk("account_id", "account"),
        UniqueConstraint("account_id", "external_id"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    account_id: Mapped[uuid.UUID]  # indexed by the unique constraint
    # Plaid investment_transaction_id (PLAN 5 ``plaid_inv_txn_id``) / SimpleFIN id.
    external_id: Mapped[str | None]
    date: Mapped[dt.date]
    type: Mapped[str]
    subtype: Mapped[str | None]
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("security.id", ondelete="SET NULL"), index=True
    )
    quantity: Mapped[Quantity | None]
    price: Mapped[Price | None]
    amount: Mapped[Money]
    fees: Mapped[Money | None]
    created_at: Mapped[CreatedAt]


class Liability(Base):
    """Loan / card terms, one row per account. Detail table (hidden from summary_only)."""

    __tablename__ = "liability"
    __table_args__ = (
        check_in("kind", LIABILITY_KINDS),
        ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            ondelete="CASCADE",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    household_id: Mapped[HouseholdId]
    kind: Mapped[str]
    apr: Mapped[Rate | None]  # percent, e.g. 24.9900
    min_payment: Mapped[Money | None]
    next_due_date: Mapped[date | None]
    statement_balance: Mapped[Money | None]
    original_principal: Mapped[Money | None]
    payoff_date: Mapped[date | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
