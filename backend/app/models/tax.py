"""Tax module tables (behind FEATURE_TAX in the app; the schema always exists).

No tax figures live here: limits and rates come from ``app/tax/config/<year>.yaml``.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import CheckConstraint, SmallInteger, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    CREATED_BY,
    FILING_STATUSES,
    TAX_SOURCE_TYPES,
    TAX_YEAR_STATUSES,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    Ratio,
    check_in,
    check_no_ssn,
    check_non_negative,
    check_ratio,
    child_fk,
    parent_key,
)


class TaxYear(Base):
    __tablename__ = "tax_year"
    __table_args__ = (
        check_in("filing_status", FILING_STATUSES, nullable=True),
        check_in("status", TAX_YEAR_STATUSES),
        CheckConstraint("state IS NULL OR state ~ '^[A-Z]{2}$'", name="state"),
        # Dependents are names / birth years / relationships. SSNs are never stored.
        check_no_ssn("(dependents::text)", "dependents_no_ssn"),
        UniqueConstraint("household_id", "year"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    year: Mapped[int] = mapped_column(SmallInteger)
    filing_status: Mapped[str | None]
    state: Mapped[str | None]  # USPS code from household settings, never hardcoded
    dependents: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(server_default=text("'open'"))
    created_at: Mapped[CreatedAt]


class TaxItem(Base):
    """One amount on one form line. ``amount`` is signed exactly as the form line is (a capital
    loss is negative, withholding is positive): not the cash-flow convention."""

    __tablename__ = "tax_item"
    __table_args__ = (
        check_in("source_type", TAX_SOURCE_TYPES),
        check_ratio("confidence"),
        child_fk("tax_year_id", "tax_year"),
        child_fk("person_id", "person", ondelete="SET NULL"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    tax_year_id: Mapped[uuid.UUID] = mapped_column(index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    form: Mapped[str]
    line_key: Mapped[str]
    amount: Mapped[Money] = mapped_column(
        comment="Signed as on the form line (losses negative), not the cash-flow convention."
    )
    source_type: Mapped[str]
    source_id: Mapped[uuid.UUID | None]  # polymorphic (document / transaction), no FK
    confidence: Mapped[Ratio | None] = mapped_column(comment="Fraction in [0, 1].")
    needs_review: Mapped[bool] = mapped_column(server_default=text("false"))
    note: Mapped[str | None]
    created_at: Mapped[CreatedAt]


class TaxScenario(Base):
    __tablename__ = "tax_scenario"
    __table_args__ = (
        check_in("created_by", CREATED_BY),
        child_fk("tax_year_id", "tax_year"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    tax_year_id: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str]
    assumptions: Mapped[dict[str, Any]]
    result: Mapped[dict[str, Any] | None]
    created_by: Mapped[str] = mapped_column(server_default=text("'user'"))
    created_at: Mapped[CreatedAt]


class EstimateSnapshot(Base):
    """A point-in-time tax estimate. Liabilities and withholding are non-negative magnitudes.
    ``projected_due`` is signed: positive = owed at filing, negative = expected refund."""

    __tablename__ = "estimate_snapshot"
    __table_args__ = (
        child_fk("tax_year_id", "tax_year"),
        check_non_negative("federal_liability"),
        check_non_negative("state_liability"),
        check_non_negative("withheld"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    tax_year_id: Mapped[uuid.UUID] = mapped_column(index=True)
    as_of: Mapped[date]
    federal_liability: Mapped[Money] = mapped_column(comment="Non-negative magnitude.")
    state_liability: Mapped[Money] = mapped_column(comment="Non-negative magnitude.")
    withheld: Mapped[Money] = mapped_column(comment="Non-negative magnitude.")
    projected_due: Mapped[Money] = mapped_column(
        comment="Positive = owed at filing, negative = expected refund."
    )
    created_at: Mapped[CreatedAt]
