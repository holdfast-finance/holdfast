"""job_run: durable record behind the arq job framework (ADR 0016).

One row per logical job. ``UNIQUE (household_id, job_name, idempotency_key)`` makes enqueue
idempotent; household_id leads it, so it also serves the FK and RLS lookups. Payloads are IDs only
(``ck_job_run_payload_ids_only``): a flat object of canonical UUID strings whose household_id
matches the row.

RLS: ENABLE + FORCE and the standard ``household_isolation`` policy (ADR 0008). The worker writes
as holdfast_app inside the job's household context. Grants come from the owner's default
privileges (SELECT/INSERT/UPDATE/DELETE for holdfast_app); nothing is narrowed: a future
retention job prunes old runs.

The CHECK patterns are copied from ``app.models._common`` (migrations never import app code).

Revision ID: 0004_job_run
Revises: 0003_app_user_household
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_job_run"
down_revision: str | None = "0003_app_user_household"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_NAME_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9:._-]*$"
UUID_TEXT_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
PAYLOAD_IDS_ONLY_SQL = (
    "jsonb_typeof(payload) = 'object'"
    " AND coalesce(payload->>'household_id', '') = household_id::text"
    ' AND NOT jsonb_path_exists(payload, \'strict $.* ? (@.type() != "string"'
    f' || !(@ like_regex "{UUID_TEXT_PATTERN}"))\')'
)


def upgrade() -> None:
    op.create_table(
        "job_run",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("job_name", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "enqueued_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retrying', 'succeeded', 'failed')",
            name=op.f("ck_job_run_status"),
        ),
        sa.CheckConstraint(
            f"length(job_name) <= 100 AND job_name ~ '{JOB_NAME_PATTERN}'",
            name=op.f("ck_job_run_job_name_format"),
        ),
        sa.CheckConstraint(
            f"length(idempotency_key) <= 200 AND idempotency_key ~ '{IDEMPOTENCY_KEY_PATTERN}'",
            name=op.f("ck_job_run_idempotency_key_format"),
        ),
        sa.CheckConstraint(PAYLOAD_IDS_ONLY_SQL, name=op.f("ck_job_run_payload_ids_only")),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 100", name=op.f("ck_job_run_max_attempts_range")
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND max_attempts", name=op.f("ck_job_run_attempts_bounded")
        ),
        sa.CheckConstraint(
            "last_error IS NULL OR length(last_error) <= 200",
            name=op.f("ck_job_run_last_error_length"),
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_job_run_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_run")),
        sa.UniqueConstraint(
            "household_id",
            "job_name",
            "idempotency_key",
            name=op.f("uq_job_run_household_id_job_name_idempotency_key"),
        ),
    )
    op.execute("ALTER TABLE job_run ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE job_run FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY household_isolation ON job_run FOR ALL"
        " USING (household_id = app_household_id())"
        " WITH CHECK (household_id = app_household_id())"
    )


def downgrade() -> None:
    # Dropping the table drops its policy, constraints and grants.
    op.drop_table("job_run")
