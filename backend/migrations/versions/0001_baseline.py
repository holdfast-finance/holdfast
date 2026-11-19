"""Baseline: enable the Postgres extensions later phases rely on.

- ``pg_trgm``: trigram indexes for fuzzy merchant/description search.
- ``pgcrypto``: ``gen_random_uuid()`` and digest helpers.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EXTENSIONS = ("pg_trgm", "pgcrypto")


def upgrade() -> None:
    for ext in EXTENSIONS:
        op.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')


def downgrade() -> None:
    for ext in reversed(EXTENSIONS):
        op.execute(f'DROP EXTENSION IF EXISTS "{ext}"')
