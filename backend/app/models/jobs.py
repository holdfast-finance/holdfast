"""Background job runs (ADR 0016). One row per logical job: the durable record behind arq."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    IDEMPOTENCY_KEY_MAX,
    IDEMPOTENCY_KEY_PATTERN,
    JOB_NAME_MAX,
    JOB_NAME_PATTERN,
    JOB_RUN_STATUSES,
    UUID_TEXT_PATTERN,
    UUIDPK,
    CreatedAt,
    check_in,
)

# Payloads are IDs only: a flat JSON object of canonical UUID strings, one of which is the row's
# own household_id. Tokens, amounts and free text never ride in Redis or sit in this table.
PAYLOAD_IDS_ONLY_SQL = (
    "jsonb_typeof(payload) = 'object'"
    " AND coalesce(payload->>'household_id', '') = household_id::text"
    ' AND NOT jsonb_path_exists(payload, \'strict $.* ? (@.type() != "string"'
    f' || !(@ like_regex "{UUID_TEXT_PATTERN}"))\')'
)


class JobRun(Base):
    """A job's lifecycle: ``queued`` → ``running`` → ``succeeded`` / ``retrying`` / ``failed``.

    ``UNIQUE (household_id, job_name, idempotency_key)`` is the dedupe: enqueueing the same key
    twice finds the existing row and never creates a second side effect. ``household_id`` leads
    that index, so it also serves the FK and RLS lookups (no separate household index).

    ``last_error`` holds the exception **class name** only. Exception messages can carry tokens
    or account numbers (hard rule 3), so they never reach this table.
    """

    __tablename__ = "job_run"
    __table_args__ = (
        UniqueConstraint("household_id", "job_name", "idempotency_key"),
        check_in("status", JOB_RUN_STATUSES),
        CheckConstraint(
            f"length(job_name) <= {JOB_NAME_MAX} AND job_name ~ '{JOB_NAME_PATTERN}'",
            name="job_name_format",
        ),
        CheckConstraint(
            f"length(idempotency_key) <= {IDEMPOTENCY_KEY_MAX}"
            f" AND idempotency_key ~ '{IDEMPOTENCY_KEY_PATTERN}'",
            name="idempotency_key_format",
        ),
        CheckConstraint(PAYLOAD_IDS_ONLY_SQL, name="payload_ids_only"),
        CheckConstraint("max_attempts BETWEEN 1 AND 100", name="max_attempts_range"),
        CheckConstraint("attempts BETWEEN 0 AND max_attempts", name="attempts_bounded"),
        CheckConstraint(
            "last_error IS NULL OR length(last_error) <= 200", name="last_error_length"
        ),
    )

    id: Mapped[UUIDPK]
    # Indexed by the unique constraint above, which leads with household_id.
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"))
    job_name: Mapped[str]
    idempotency_key: Mapped[str]
    payload: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    max_attempts: Mapped[int]
    last_error: Mapped[str | None]
    enqueued_at: Mapped[CreatedAt]
    started_at: Mapped[datetime | None]
    next_attempt_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
