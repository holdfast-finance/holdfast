"""Uploaded documents (statements, tax forms, receipts, credit reports) and parsed fields."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, SmallInteger, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    DOCUMENT_KINDS,
    DOCUMENT_PARSE_STATUSES,
    UUIDPK,
    HouseholdId,
    Ratio,
    check_in,
    check_no_ssn,
    check_ratio,
    child_fk,
    parent_key,
)


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        check_in("kind", DOCUMENT_KINDS),
        check_in("parse_status", DOCUMENT_PARSE_STATUSES),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        child_fk("person_id", "person", ondelete="SET NULL"),
        UniqueConstraint("household_id", "sha256"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    person_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    kind: Mapped[str]
    tax_year: Mapped[int | None] = mapped_column(SmallInteger)
    filename: Mapped[str]
    sha256: Mapped[str]  # lower-case hex; dedupes re-uploads per household
    storage_path: Mapped[str]
    mime: Mapped[str]
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    parse_status: Mapped[str] = mapped_column(server_default=text("'pending'"))


class ParsedField(Base):
    """A value extracted from a document. ``value`` may never hold an SSN-shaped number (CHECK):
    parsers must mask SSNs (``XXX-XX-1234``) before storing. ``confidence`` is a fraction."""

    __tablename__ = "parsed_field"
    __table_args__ = (
        child_fk("document_id", "document"),
        check_no_ssn("value", "value_no_ssn"),
        check_ratio("confidence"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    document_id: Mapped[uuid.UUID] = mapped_column(index=True)
    key: Mapped[str]
    value: Mapped[str | None]
    confidence: Mapped[Ratio | None] = mapped_column(comment="Fraction in [0, 1].")
    page: Mapped[int | None]
    bbox: Mapped[dict[str, Any] | None]
    verified_by_user: Mapped[bool] = mapped_column(server_default=text("false"))
