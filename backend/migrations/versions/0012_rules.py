"""Author-scoped category rules and transaction rule provenance.

Revision ID: 0012_rules
Revises: 0011_connector_link
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_rules"
down_revision = "0011_connector_link"
branch_labels = None
depends_on = None

# Frozen predicates from 0002. The account subqueries run under the caller's RLS.
ACCOUNT_HELPERS_SQL = """
CREATE FUNCTION public.app_account_details_visible(p_account_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER
    RETURN EXISTS (SELECT 1 FROM public.account a WHERE a.id = p_account_id
                   AND (a.visibility = 'shared' OR a.owner_person_id IS NULL
                        OR a.owner_person_id = public.app_person_id()
                        OR public.app_household_wide()));
CREATE FUNCTION public.app_account_writable(p_account_id uuid) RETURNS boolean
    LANGUAGE sql STABLE SECURITY INVOKER
    RETURN public.app_household_wide() OR EXISTS (
        SELECT 1 FROM public.account a WHERE a.id = p_account_id
                   AND (a.owner_person_id IS NULL OR a.owner_person_id = public.app_person_id()));
REVOKE ALL ON FUNCTION public.app_account_details_visible(uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.app_account_writable(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.app_account_details_visible(uuid) TO holdfast_app;
GRANT EXECUTE ON FUNCTION public.app_account_writable(uuid) TO holdfast_app;
"""

RULE_TRIGGERS_SQL = """
CREATE FUNCTION public.rule_scope_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.author_person_id IS DISTINCT FROM OLD.author_person_id THEN
        RAISE EXCEPTION 'cannot change author of rule %', OLD.id USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION public.rule_scope_guard() FROM PUBLIC;
CREATE TRIGGER rule_scope_guard BEFORE UPDATE OF author_person_id ON public.rule
    FOR EACH ROW EXECUTE FUNCTION public.rule_scope_guard();

CREATE FUNCTION public.rule_caps() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    prev_wide text := current_setting('app.household_wide', true);
    author_count bigint;
    regex_count bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('holdfast.rule_caps'), hashtext(NEW.household_id::text));
    PERFORM set_config('app.household_wide', 'on', true);
    SELECT count(*) FILTER (WHERE r.author_person_id = NEW.author_person_id),
           count(*) FILTER (
               WHERE r.enabled AND r.conditions->'description'->>'mode' = 'regex'
           )
      INTO author_count, regex_count
      FROM public.rule r
     WHERE r.household_id = NEW.household_id AND r.id <> NEW.id;
    PERFORM set_config('app.household_wide', coalesce(prev_wide, ''), true);

    IF author_count >= 200 THEN
        RAISE EXCEPTION 'rule author cap exceeded' USING ERRCODE = '23514';
    END IF;
    IF NEW.enabled AND NEW.conditions->'description'->>'mode' = 'regex' AND regex_count >= 25 THEN
        RAISE EXCEPTION 'enabled regex rule cap exceeded' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
REVOKE ALL ON FUNCTION public.rule_caps() FROM PUBLIC;
CREATE TRIGGER rule_caps BEFORE INSERT OR UPDATE OF enabled, conditions ON public.rule
    FOR EACH ROW EXECUTE FUNCTION public.rule_caps();
"""

RULE_CHECKS = {
    "name": "char_length(name) BETWEEN 1 AND 80",
    "priority": "priority BETWEEN 1 AND 10000",
    "conditions": (
        "jsonb_typeof(conditions) = 'object' AND conditions->>'v' IS NOT DISTINCT FROM '1'"
        " AND (NOT conditions ? 'amount_equals' OR "
        "jsonb_typeof(conditions->'amount_equals') = 'string')"
        " AND (NOT conditions ? 'amount_min' OR "
        "jsonb_typeof(conditions->'amount_min') = 'string')"
        " AND (NOT conditions ? 'amount_max' OR "
        "jsonb_typeof(conditions->'amount_max') = 'string')"
    ),
    "account_scope": "account_scope IN ('any', 'joint', 'own')",
}
RULE_FKS = {"author_person_id": "person", "category_id": "category"}
RULE_ORDER_INDEX = "ix_rule_household_id_priority_created_at_id"
TXN_RULE_FK = "fk_transaction_category_rule_id_rule"
TXN_RULE_CHECK = "ck_transaction_category_rule_source"
TXN_RULE_INDEX = "ix_transaction_category_rule_id"


def _require_empty(direction: str) -> None:
    # This takes the table lock in the migration transaction, and lets the owner count across
    # households before FORCE RLS is restored.
    op.execute("ALTER TABLE public.rule NO FORCE ROW LEVEL SECURITY")
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM public.rule")).scalar_one()
    op.execute("ALTER TABLE public.rule FORCE ROW LEVEL SECURITY")
    if count:
        raise RuntimeError(f"cannot {direction} 0012: rule contains {count} row(s)")


def upgrade() -> None:
    _require_empty("upgrade")
    op.execute(ACCOUNT_HELPERS_SQL)

    op.drop_column("rule", "set")
    op.drop_column("rule", "hit_count")
    op.alter_column("rule", "match", new_column_name="conditions")
    for column in ("author_person_id", "category_id"):
        op.add_column("rule", sa.Column(column, sa.Uuid(), nullable=False))
    op.add_column("rule", sa.Column("name", sa.Text(), nullable=False))
    op.add_column(
        "rule", sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "rule",
        sa.Column("account_scope", sa.Text(), nullable=False, server_default=sa.text("'own'")),
    )
    op.add_column("rule", sa.Column("last_applied_at", sa.DateTime(timezone=True)))
    op.add_column(
        "rule",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    for name, predicate in RULE_CHECKS.items():
        op.create_check_constraint(op.f(f"ck_rule_{name}"), "rule", predicate)
    for column, parent in RULE_FKS.items():
        op.create_foreign_key(
            op.f(f"fk_rule_{column}_{parent}"),
            "rule",
            parent,
            [column, "household_id"],
            ["id", "household_id"],
            ondelete="CASCADE",
        )
    op.create_unique_constraint(op.f("uq_rule_id_household_id"), "rule", ["id", "household_id"])
    op.create_index("ix_rule_author_person_id", "rule", ["author_person_id"])
    op.create_index("ix_rule_category_id", "rule", ["category_id"])
    op.create_index(RULE_ORDER_INDEX, "rule", ["household_id", "priority", "created_at", "id"])

    author = "public.app_household_wide() OR author_person_id = public.app_person_id()"
    op.execute(
        "CREATE POLICY person_read ON public.rule AS RESTRICTIVE FOR SELECT"
        f" USING ({author} OR account_scope IN ('any', 'joint'))"
    )
    op.execute(
        "CREATE POLICY person_insert ON public.rule AS RESTRICTIVE FOR INSERT"
        f" WITH CHECK ({author})"
    )
    op.execute(
        "CREATE POLICY person_update ON public.rule AS RESTRICTIVE FOR UPDATE"
        f" USING ({author}) WITH CHECK ({author})"
    )
    op.execute(
        f"CREATE POLICY person_delete ON public.rule AS RESTRICTIVE FOR DELETE USING ({author})"
    )
    op.execute(RULE_TRIGGERS_SQL)

    op.add_column("transaction", sa.Column("category_rule_id", sa.Uuid()))
    op.create_foreign_key(
        op.f(TXN_RULE_FK),
        "transaction",
        "rule",
        ["category_rule_id", "household_id"],
        ["id", "household_id"],
        ondelete="SET NULL (category_rule_id)",
    )
    op.create_check_constraint(
        op.f(TXN_RULE_CHECK),
        "transaction",
        "category_rule_id IS NULL OR category_source IS NOT DISTINCT FROM 'rule'",
    )
    op.create_index(
        TXN_RULE_INDEX,
        "transaction",
        ["category_rule_id"],
        postgresql_where=sa.text("category_rule_id IS NOT NULL"),
    )


def downgrade() -> None:
    _require_empty("downgrade")

    op.drop_index(TXN_RULE_INDEX, table_name="transaction")
    op.drop_constraint(op.f(TXN_RULE_CHECK), "transaction", type_="check")
    op.drop_constraint(op.f(TXN_RULE_FK), "transaction", type_="foreignkey")
    op.drop_column("transaction", "category_rule_id")

    for trigger in ("rule_caps", "rule_scope_guard"):
        op.execute(f"DROP TRIGGER {trigger} ON public.rule")
        op.execute(f"DROP FUNCTION public.{trigger}()")
    for policy in ("person_read", "person_insert", "person_update", "person_delete"):
        op.execute(f"DROP POLICY {policy} ON public.rule")
    op.drop_index(RULE_ORDER_INDEX, table_name="rule")
    op.drop_index("ix_rule_category_id", table_name="rule")
    op.drop_index("ix_rule_author_person_id", table_name="rule")
    op.drop_constraint(op.f("uq_rule_id_household_id"), "rule", type_="unique")
    for column, parent in RULE_FKS.items():
        op.drop_constraint(op.f(f"fk_rule_{column}_{parent}"), "rule", type_="foreignkey")
    for name in RULE_CHECKS:
        op.drop_constraint(op.f(f"ck_rule_{name}"), "rule", type_="check")
    for column in (
        "updated_at",
        "last_applied_at",
        "account_scope",
        "enabled",
        "name",
        "category_id",
        "author_person_id",
    ):
        op.drop_column("rule", column)
    op.alter_column("rule", "conditions", new_column_name="match")
    op.add_column("rule", sa.Column("set", postgresql.JSONB(), nullable=False))
    op.add_column(
        "rule", sa.Column("hit_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )

    op.execute("DROP FUNCTION public.app_account_details_visible(uuid)")
    op.execute("DROP FUNCTION public.app_account_writable(uuid)")
