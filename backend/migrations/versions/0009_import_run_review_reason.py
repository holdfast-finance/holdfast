"""Store why an import run was flagged (I1c).

``import_run.review_reason`` holds the parser's stable code(s) for a run created as
``needs_review`` (``balance_mismatch``, ``missing_balance``, ``date_out_of_period``,
``unexplained_duplicates``; tabular profiles may comma-join several). Nullable: reconciled runs
carry none, and runs flagged before this column existed stay NULL (the service reports them as
inferred or ``unknown``; nothing is backfilled by re-parsing stored documents).

RLS is unchanged: ``import_run`` keeps its existing policies and the ``import_run_guard`` trigger
does not freeze this column (it is written in the same statement that sets the flag).

Revision ID: 0009_import_run_review_reason
Revises: 0008_category_tree
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_import_run_review_reason"
down_revision: str | None = "0008_category_tree"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_run", sa.Column("review_reason", sa.String(), nullable=True))
    op.create_check_constraint(
        op.f("ck_import_run_review_reason_format"),
        "import_run",
        "review_reason IS NULL OR (length(review_reason) <= 120"
        " AND review_reason ~ '^[a-z][a-z0-9_]*(,[a-z][a-z0-9_]*)*$')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_import_run_review_reason_format"), "import_run", type_="check")
    op.drop_column("import_run", "review_reason")
