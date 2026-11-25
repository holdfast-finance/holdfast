"""Index for the live-transaction keyset list (U1a).

``GET /api/transactions`` pages live rows (superseded excluded) by ``posted_date DESC, id DESC``
across every account the caller can see. A partial index on exactly that order lets Postgres walk
the first page and each cursor page without sorting the household's whole history.

Revision ID: 0007_transaction_live_index
Revises: 0006_account_import_unique
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_transaction_live_index"
down_revision: str | None = "0006_account_import_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_transaction_household_id_posted_date_id_live"


def upgrade() -> None:
    op.create_index(
        INDEX,
        "transaction",
        ["household_id", sa.text("posted_date DESC"), sa.text("id DESC")],
        postgresql_where=sa.text("superseded_by_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="transaction")
