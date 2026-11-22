"""list_household_ids(): enumerate households without an RLS context (ADR 0015).

``household`` has FORCE RLS (``id = app_household_id()``), so an unscoped session sees no rows
and ``rotate-keys`` could only find households through ``app_user``. A household with no user
(a half-finished setup, a removed last user) then kept legacy ciphertext that neither the
rotation pass nor the residual scan could reach.

``list_household_ids()`` is SECURITY DEFINER and returns ids only. FORCE RLS binds the function
owner too, so a SELECT-only policy scoped ``TO holdfast_owner`` lets the owner read the table.
The app role gets no new policy, no new table privilege and no row access: only EXECUTE on this
function, which exposes UUIDs (no names, no data).

Revision ID: 0010_list_household_ids
Revises: 0009_import_run_review_reason
Create Date: 2026-11-20
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_list_household_ids"
down_revision: str | None = "0009_import_run_review_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "holdfast_app"
OWNER_ROLE = "holdfast_owner"

FUNCTION_SQL = """
CREATE FUNCTION list_household_ids() RETURNS SETOF uuid
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
    SELECT id FROM public.household ORDER BY id
$$;
COMMENT ON FUNCTION list_household_ids() IS
    'Ids of every household, for background jobs (rotate-keys). Ids only, no other columns.';
REVOKE ALL ON FUNCTION list_household_ids() FROM PUBLIC;
"""


def upgrade() -> None:
    op.execute(
        f"CREATE POLICY household_list_owner ON household FOR SELECT TO {OWNER_ROLE} USING (true)"
    )
    op.execute(FUNCTION_SQL)
    op.execute(f"GRANT EXECUTE ON FUNCTION list_household_ids() TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION list_household_ids()")
    op.execute("DROP POLICY household_list_owner ON household")
