"""app_user.household_id: let login resolve the tenant without reading RLS-protected rows.

``person`` has FORCE RLS keyed on the household, so an unscoped session (login, before any
context exists) can't follow ``app_user.person_id`` to a household. ``app_user`` has no RLS, so a
denormalized ``household_id`` there is the lookup. A composite FK ``(person_id, household_id) ->
person(id, household_id)`` keeps it consistent with the person whenever ``person_id`` is set.

Backfill: existing users (if any) get their person's household. ``person`` has FORCE RLS, which
binds this migration's role too, so the backfill drops FORCE for the statement and restores it.

Revision ID: 0003_app_user_household
Revises: 0002_core_schema
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_app_user_household"
down_revision: str | None = "0002_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("household_id", sa.Uuid(), nullable=True))
    op.execute("ALTER TABLE person NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "UPDATE app_user u SET household_id = p.household_id FROM person p WHERE p.id = u.person_id"
    )
    op.execute("ALTER TABLE person FORCE ROW LEVEL SECURITY")
    # A user with no person can't be placed in a household; there are none before this phase.
    op.execute("DELETE FROM app_user WHERE household_id IS NULL")
    op.alter_column("app_user", "household_id", nullable=False)
    op.create_index(op.f("ix_app_user_household_id"), "app_user", ["household_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_app_user_household_id_household"),
        "app_user",
        "household",
        ["household_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_app_user_person_household",
        "app_user",
        "person",
        ["person_id", "household_id"],
        ["id", "household_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_app_user_person_household", "app_user", type_="foreignkey")
    op.drop_constraint(op.f("fk_app_user_household_id_household"), "app_user", type_="foreignkey")
    op.drop_index(op.f("ix_app_user_household_id"), table_name="app_user")
    op.drop_column("app_user", "household_id")
