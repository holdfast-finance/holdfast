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
    check_non_negative,
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
    """Position per account, security and day. Detail table (hidden from summary_only).

    ``quantity`` is positive for a long position (negative only for a short). ``cost_basis`` and
    ``value`` are totals for the position in account currency, positive for a long position.
    """

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
    """Buys, sells, dividends, contributions, fees. Detail table (hidden from summary_only).

    Sign convention (same as ``transaction``: cash out of the account is negative):

    - ``amount``: cash effect on the account. Buy negative (outflow), sell positive, dividend /
      interest positive, fee negative. Plaid reports buys as positive, so negate on ingest.
    - ``quantity``: change in units held. Buy positive, sell negative.
    - ``price``: per unit, non-negative (CHECK).
    - ``fees``: a non-negative magnitude (CHECK ``>= 0``), already included in ``amount``.
    """

    __tablename__ = "investment_txn"
    __table_args__ = (
        check_in("type", INVESTMENT_TXN_TYPES),
        check_non_negative("fees"),
        check_non_negative("price"),
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
    quantity: Mapped[Quantity | None] = mapped_column(
        comment="Change in units held: buy positive, sell negative."
    )
    price: Mapped[Price | None] = mapped_column(comment="Per-unit price, non-negative.")
    amount: Mapped[Money] = mapped_column(
        comment="Cash effect: buy negative (outflow), sell/dividend positive. Plaid negated."
    )
    fees: Mapped[Money | None] = mapped_column(
        comment="Fees as a non-negative magnitude, already included in amount."
    )
    created_at: Mapped[CreatedAt]


class Liability(Base):
    """Loan / card terms, one row per account. Detail table (hidden from summary_only).

    Amounts here are as the lender states them: ``statement_balance`` is positive = owed
    (negative only for a credit balance), ``min_payment`` and ``original_principal`` are
    non-negative magnitudes. This differs on purpose from ``balance_snapshot.current``, which
    is negative for debt. ``apr`` is a **percent** (24.9900 = 24.99%), unlike Ratio fractions.
    """

    __tablename__ = "liability"
    __table_args__ = (
        check_in("kind", LIABILITY_KINDS),
        check_non_negative("apr"),
        check_non_negative("min_payment"),
        check_non_negative("original_principal"),
        ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            ondelete="CASCADE",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    household_id: Mapped[HouseholdId]
    kind: Mapped[str]
    apr: Mapped[Rate | None] = mapped_column(
        comment="Annual percentage rate as a percent: 24.9900 = 24.99% (not a fraction)."
    )
    min_payment: Mapped[Money | None] = mapped_column(comment="Non-negative magnitude.")
    next_due_date: Mapped[date | None]
    statement_balance: Mapped[Money | None] = mapped_column(
        comment="As stated by the lender: positive = owed, negative = credit balance."
    )
    original_principal: Mapped[Money | None] = mapped_column(comment="Non-negative magnitude.")
    payoff_date: Mapped[date | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
