"""Budgets and budget lines. ``budget_period_actual`` (materialized view) arrives in Phase 5."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    BUDGET_METHODS,
    BUDGET_PERIODS,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    Ratio,
    check_in,
    child_fk,
    parent_key,
)


class Budget(Base):
    __tablename__ = "budget"
    __table_args__ = (
        check_in("period", BUDGET_PERIODS),
        check_in("method", BUDGET_METHODS),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    name: Mapped[str]
    period: Mapped[str] = mapped_column(server_default=text("'monthly'"))
    method: Mapped[str] = mapped_column(server_default=text("'category'"))
    rollover: Mapped[bool] = mapped_column(server_default=text("false"))
    start_date: Mapped[date]
    active: Mapped[bool] = mapped_column(server_default=text("true"))
    created_at: Mapped[CreatedAt]


class BudgetLine(Base):
    __tablename__ = "budget_line"
    __table_args__ = (
        child_fk("budget_id", "budget"),
        child_fk("category_id", "category"),
        UniqueConstraint("budget_id", "category_id"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    budget_id: Mapped[uuid.UUID]  # indexed by the unique constraint
    category_id: Mapped[uuid.UUID] = mapped_column(index=True)
    amount: Mapped[Money]
    alert_pct: Mapped[Ratio] = mapped_column(server_default=text("0.8"))
