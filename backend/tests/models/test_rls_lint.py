"""RLS lint: every table that carries ``household_id`` is protected, so a new table can't ship open.

Complements ``test_schema.py`` by reading ``pg_class`` / ``pg_policies`` directly with no table
allowlist other than the documented identity exception.
"""

from __future__ import annotations

from sqlalchemy import Engine, text

# app_user.household_id (migration 0003) is the login lookup that runs before any RLS context
# exists (ADR 0010). Identity tables are guarded by the API layer instead.
UNPROTECTED_BY_DESIGN = {"app_user"}


def test_every_household_id_table_has_enable_force_rls_and_a_policy(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT cl.relname, cl.relrowsecurity, cl.relforcerowsecurity,"
                "  (SELECT count(*) FROM pg_policies p"
                "    WHERE p.schemaname = 'public' AND p.tablename = cl.relname) AS policies"
                " FROM pg_class cl"
                " JOIN information_schema.columns col"
                "   ON col.table_schema = 'public' AND col.table_name = cl.relname"
                "  AND col.column_name = 'household_id'"
                " WHERE cl.relnamespace = 'public'::regnamespace AND cl.relkind = 'r'"
            )
        ).all()
    assert rows, "no household_id tables found; the lint would be vacuous"
    offenders = [
        f"{name}: enable={enabled} force={forced} policies={policies}"
        for name, enabled, forced, policies in rows
        if name not in UNPROTECTED_BY_DESIGN and not (enabled and forced and policies >= 1)
    ]
    detail = "\n".join(offenders)
    assert not offenders, f"household_id tables lacking ENABLE+FORCE RLS and a policy:\n{detail}"
