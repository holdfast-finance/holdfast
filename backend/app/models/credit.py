"""Credit reports (manual AnnualCreditReport.com import), tradelines, inquiries and scores."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    CREDIT_BUREAUS,
    INQUIRY_KINDS,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    check_in,
    check_masked,
    child_fk,
    parent_key,
)


class CreditReport(Base):
    __tablename__ = "credit_report"
    __table_args__ = (
        check_in("bureau", CREDIT_BUREAUS),
        child_fk("person_id", "person"),
        child_fk("document_id", "document", ondelete="SET NULL"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    person_id: Mapped[uuid.UUID] = mapped_column(index=True)
    bureau: Mapped[str]
    pulled_on: Mapped[dt.date]
    document_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    parsed_ok: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[CreatedAt]


class Tradeline(Base):
    """One account on a credit report. ``account_number_masked`` rejects full numbers."""

    __tablename__ = "tradeline"
    __table_args__ = (
        check_masked("account_number_masked"),
        child_fk("credit_report_id", "credit_report"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    credit_report_id: Mapped[uuid.UUID] = mapped_column(index=True)
    creditor: Mapped[str]
    account_type: Mapped[str | None]
    opened: Mapped[dt.date | None]
    status: Mapped[str | None]
    credit_limit: Mapped[Money | None]
    balance: Mapped[Money | None]
    high_balance: Mapped[Money | None]
    payment_history: Mapped[dict[str, Any] | None]
    account_number_masked: Mapped[str | None]


class CreditInquiry(Base):
    __tablename__ = "credit_inquiry"
    __table_args__ = (
        check_in("kind", INQUIRY_KINDS),
        child_fk("credit_report_id", "credit_report"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    credit_report_id: Mapped[uuid.UUID] = mapped_column(index=True)
    creditor: Mapped[str]
    date: Mapped[dt.date]
    kind: Mapped[str]


class CreditScoreEntry(Base):
    __tablename__ = "credit_score_entry"
    __table_args__ = (
        check_in("bureau", CREDIT_BUREAUS, nullable=True),
        CheckConstraint("score BETWEEN 250 AND 990", name="score_range"),
        child_fk("person_id", "person"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    person_id: Mapped[uuid.UUID] = mapped_column(index=True)
    model: Mapped[str]  # e.g. 'FICO 8', 'VantageScore 3.0'
    bureau: Mapped[str | None]
    score: Mapped[int]
    as_of: Mapped[dt.date]
    source: Mapped[str | None]
    created_at: Mapped[CreatedAt]
