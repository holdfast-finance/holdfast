"""Uploaded documents (statements, tax forms, receipts, credit reports) and parsed fields."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, SmallInteger, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    DOCUMENT_KINDS,
    DOCUMENT_PARSE_STATUSES,
    PROFILE_ID_MAX,
    PROFILE_ID_PATTERN,
    SHA256_HEX_PATTERN,
    STORAGE_PATH_MAX,
    STORAGE_PATH_PATTERN,
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
    """An uploaded file: statement, export, tax form, receipt or credit report (ADR 0017).

    The bytes live on disk under ``DOCUMENT_STORE``; the database holds only metadata, never
    the content. ``storage_path`` is relative to the store (CHECK: no leading slash, no ``.`` or
    ``..`` segments). ``UNIQUE (household_id, sha256)`` makes re-uploading the same file return
    the existing row (PLAN 8.1 step 1).

    ``parse_status`` stays ``needs_review`` while ``import_review_latched`` is set. A trigger
    sets the latch when a run is flagged and clears it only when a run is accepted and no
    sibling run still needs review. Deleting the run does not clear the latch.
    """

    __tablename__ = "document"
    __table_args__ = (
        check_in("kind", DOCUMENT_KINDS),
        check_in("parse_status", DOCUMENT_PARSE_STATUSES),
        CheckConstraint(f"sha256 ~ '{SHA256_HEX_PATTERN}'", name="sha256_hex"),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint(
            f"length(storage_path) <= {STORAGE_PATH_MAX}"
            f" AND storage_path ~ '{STORAGE_PATH_PATTERN}'",
            name="storage_path_relative",
        ),
        CheckConstraint(
            f"detected_profile IS NULL OR (length(detected_profile) <= {PROFILE_ID_MAX}"
            f" AND detected_profile ~ '{PROFILE_ID_PATTERN}')",
            name="detected_profile_format",
        ),
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
    # Relative to DOCUMENT_STORE (CHECK ``storage_path_relative``). Content is never in the DB.
    storage_path: Mapped[str]
    mime: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Bank profile id matched by text fingerprints (PLAN 8.1 step 2); NULL until detected.
    detected_profile: Mapped[str | None]
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    parse_status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    # Set when a run needs review. Cleared only by accepting that run (ADR 0017).
    import_review_latched: Mapped[bool] = mapped_column(server_default=text("false"))


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
