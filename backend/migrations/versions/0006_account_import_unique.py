"""Imported accounts are unique per household (statement import, I1).

An imported account has no bank connection, so ``UNIQUE (bank_connection_id,
external_account_id)`` never fires (NULLs are distinct). The import service keys an account by
``external_account_id`` (profile, last four digits, block hash); this partial unique index makes
that key unique per household so two racing imports cannot create twins. Plaid and SimpleFIN
accounts are unaffected.

Revision ID: 0006_account_import_unique
Revises: 0005_import_sync_runs
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_account_import_unique"
down_revision: str | None = "0005_import_sync_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_account_household_id_external_account_id_import",
        "account",
        ["household_id", "external_account_id"],
        unique=True,
        postgresql_where="source = 'import'",
    )


def downgrade() -> None:
    op.drop_index("uq_account_household_id_external_account_id_import", table_name="account")
