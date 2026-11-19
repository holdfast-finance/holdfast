"""Core schema: PLAN 5 tables, RLS, per-person visibility, create_household().

Tables come from autogenerate (reviewed by hand). Autogenerate can't see the rest, so it's
written out below and documented in ADR 0008:

- ``app_household_id()`` / ``app_person_id()`` / ``app_household_wide()``: STABLE SQL helpers
  over transaction-local GUCs set by ``app.db_context``. Unset returns NULL / false (fail closed).
- ENABLE + FORCE RLS and a ``household_isolation`` policy on every household-scoped table.
- RESTRICTIVE per-person policies (PLAN 20.3) on account, its detail tables and balance_snapshot:
  a **read** policy (visibility) and separate **write** policies (owner, joint or household-wide
  only), so seeing a shared / summary_only account never grants the right to change it.
- ``account_guard_ownership``: BEFORE UPDATE trigger, only the owner (or anyone, for a joint
  account, or a household-wide job) may change ``visibility`` / ``owner_person_id``.
- ``transaction_split_balance``: deferred constraint trigger, a transaction's splits sum exactly
  to its amount with the same sign.
- RESTRICTIVE person scoping on agent_conversation / agent_message / agent_finding.
- ``audit_log``: append-only for the app role (INSERT + SELECT, no UPDATE / DELETE), no FKs.
- ``create_household(name)``: SECURITY DEFINER, the only way to create a household without context.

Grants: ``infra/postgres/init/00-roles.sh`` sets ALTER DEFAULT PRIVILEGES so every table the owner
creates gets SELECT/INSERT/UPDATE/DELETE for holdfast_app (and nothing else: no TRUNCATE, no DDL).
This migration only *narrows* that (audit_log, household, institution, security).
``tests/models/test_rls_audit_and_roles.py`` asserts the privilege set table by table.

Revision ID: 0002_core_schema
Revises: 0001_baseline
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_core_schema"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_ROLE = "holdfast_app"

# Same pattern as app.models._common.SSN_PATTERN (copied: migrations never import app code).
SSN_PATTERN = "(^|[^0-9])[0-9]{3}([- ]?)[0-9]{2}\\2[0-9]{4}([^0-9]|$)"

# household_isolation: household_id = app_household_id() (USING and WITH CHECK).
HOUSEHOLD_TABLES = (
    "person",
    "household_setting",
    "bank_connection",
    "account",
    "balance_snapshot",
    "category",
    "rule",
    "transaction",
    "transaction_split",
    "holding",
    "investment_txn",
    "liability",
    "budget",
    "budget_line",
    "credit_report",
    "tradeline",
    "credit_inquiry",
    "credit_score_entry",
    "document",
    "parsed_field",
    "tax_year",
    "tax_item",
    "tax_scenario",
    "estimate_snapshot",
    "agent_conversation",
    "agent_message",
    "agent_finding",
)

# Shared reference data, no RLS. The app may read, add and refresh rows but not delete them.
REFERENCE_TABLES = ("institution", "security")

# ---- per-person policies (PLAN 20.3, ADR 0008)
# Account rows the viewer may *see*.
ACCOUNT_VISIBLE = (
    "visibility IN ('shared', 'summary_only')"
    " OR owner_person_id IS NULL"
    " OR owner_person_id = app_person_id()"
    " OR app_household_wide()"
)
# Account rows the viewer may *write*: joint, theirs, or a household-wide job. Shared and
# summary_only accounts are read-only to everyone but their owner.
ACCOUNT_WRITABLE = (
    "owner_person_id IS NULL OR owner_person_id = app_person_id() OR app_household_wide()"
)
# Accounts whose details (transactions, holdings, ...) the viewer may see: not summary_only for
# someone else. The subquery on account is itself filtered by account's RLS.
ACCOUNT_DETAIL_VISIBLE = (
    "EXISTS (SELECT 1 FROM account a WHERE a.id = {table}.account_id AND ("
    "a.visibility = 'shared' OR a.owner_person_id IS NULL"
    " OR a.owner_person_id = app_person_id() OR app_household_wide()))"
)
# Detail rows the viewer may write: the row's account is writable. Used for UPDATE's USING (old
# account) and WITH CHECK (new account), so moving a row between accounts needs both.
ACCOUNT_DETAIL_WRITABLE = (
    "app_household_wide() OR EXISTS (SELECT 1 FROM account a WHERE a.id = {table}.account_id"
    " AND (a.owner_person_id IS NULL OR a.owner_person_id = app_person_id()))"
)
ACCOUNT_DETAIL_TABLES = ("transaction", "holding", "investment_txn", "liability")

# table -> (read predicate, write predicate)
PERSON_POLICIES: dict[str, tuple[str, str]] = {
    "account": (ACCOUNT_VISIBLE, ACCOUNT_WRITABLE),
    **{
        t: (
            ACCOUNT_DETAIL_VISIBLE.format(table=f'"{t}"'),
            ACCOUNT_DETAIL_WRITABLE.format(table=f'"{t}"'),
        )
        for t in ACCOUNT_DETAIL_TABLES
    },
    # Splits inherit their transaction's visibility (transaction RLS applies in the subquery) and
    # its account's write rule.
    "transaction_split": (
        'EXISTS (SELECT 1 FROM "transaction" t WHERE t.id = transaction_split.transaction_id)',
        'app_household_wide() OR EXISTS (SELECT 1 FROM "transaction" t'
        " JOIN account a ON a.id = t.account_id"
        " WHERE t.id = transaction_split.transaction_id"
        " AND (a.owner_person_id IS NULL OR a.owner_person_id = app_person_id()))",
    ),
    # Balances follow account visibility, so summary_only viewers still see them. Writes follow
    # the account write rule.
    "balance_snapshot": (
        "EXISTS (SELECT 1 FROM account a WHERE a.id = balance_snapshot.account_id)",
        ACCOUNT_DETAIL_WRITABLE.format(table="balance_snapshot"),
    ),
}

# Agent output inherits the run's visibility: a run for a person may have read their private
# accounts, so it's visible only to them (or household-wide jobs). NULL person = household run.
AGENT_PERSON_SCOPED = "person_id IS NULL OR person_id = app_person_id() OR app_household_wide()"
AGENT_POLICIES: dict[str, str] = {
    "agent_conversation": AGENT_PERSON_SCOPED,
    "agent_finding": AGENT_PERSON_SCOPED,
    # Messages inherit their conversation's scope (agent_conversation RLS applies).
    "agent_message": (
        "EXISTS (SELECT 1 FROM agent_conversation c WHERE c.id = agent_message.conversation_id)"
    ),
}

HELPER_FUNCTIONS_SQL = """
CREATE FUNCTION app_household_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT NULLIF(current_setting('app.household_id', true), '')::uuid $$;
COMMENT ON FUNCTION app_household_id() IS
    'RLS: current household (transaction-local GUC app.household_id). NULL when unset.';

CREATE FUNCTION app_person_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT NULLIF(current_setting('app.person_id', true), '')::uuid $$;
COMMENT ON FUNCTION app_person_id() IS
    'RLS: viewing person (transaction-local GUC app.person_id). NULL when unset.';

CREATE FUNCTION app_household_wide() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    AS $$ SELECT coalesce(current_setting('app.household_wide', true), '') = 'on' $$;
COMMENT ON FUNCTION app_household_wide() IS
    'RLS: background jobs only. Widens per-person visibility inside the current household.';
"""

CREATE_HOUSEHOLD_SQL = """
CREATE FUNCTION create_household(name text) RETURNS uuid
    LANGUAGE plpgsql
    VOLATILE
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    new_id uuid := gen_random_uuid();
    prev_household text := current_setting('app.household_id', true);
BEGIN
    IF create_household.name IS NULL OR btrim(create_household.name) = '' THEN
        RAISE EXCEPTION 'household name is required' USING ERRCODE = '22023';
    END IF;
    -- household has FORCE RLS, which binds this function's owner too: scope the insert to the
    -- new id, then restore the caller's context.
    PERFORM set_config('app.household_id', new_id::text, true);
    INSERT INTO public.household (id, name) VALUES (new_id, btrim(create_household.name));
    PERFORM set_config('app.household_id', coalesce(prev_household, ''), true);
    RETURN new_id;
END
$$;
COMMENT ON FUNCTION create_household(text) IS
    'First-run setup: the only way to create a household without an RLS context.';
REVOKE ALL ON FUNCTION create_household(text) FROM PUBLIC;
"""

# Defense in depth behind the account write policies: even if a future policy change let a
# non-owner UPDATE an account row, they still couldn't change who sees it or who owns it.
# coalesce(..., false): a NULL person must not make the test pass.
ACCOUNT_GUARD_SQL = """
CREATE FUNCTION account_guard_ownership() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF (NEW.visibility IS DISTINCT FROM OLD.visibility
        OR NEW.owner_person_id IS DISTINCT FROM OLD.owner_person_id)
       AND NOT coalesce(
           OLD.owner_person_id IS NULL
           OR OLD.owner_person_id = app_person_id()
           OR app_household_wide(),
           false)
    THEN
        RAISE EXCEPTION 'only the account owner may change its visibility or owner'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION account_guard_ownership() IS
    'Rejects visibility / owner changes on an account by anyone but its owner (ADR 0008).';
CREATE TRIGGER account_guard_ownership
    BEFORE UPDATE ON account
    FOR EACH ROW EXECUTE FUNCTION account_guard_ownership();
"""

# Deferred so a unit of work can rewrite a transaction's splits (or its amount and splits) in
# any order; the invariant is checked once per affected row at COMMIT. The function runs as the
# invoker but switches app.household_wide on for its own queries (then restores the caller's
# value) so it sees every split of the parent regardless of who is viewing; the household
# boundary still applies. A function-level ``SET app.household_wide`` would be cleaner, but
# Postgres reserves SET clauses on unregistered placeholders to superusers. If the check raises,
# the (sub)transaction aborts and the GUC change is rolled back with it.
# A missing parent means it was deleted (splits cascade), so there's nothing to check. Messages
# carry only the transaction id, never amounts.
SPLIT_BALANCE_SQL = """
CREATE FUNCTION transaction_split_balance() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    prev_wide text := current_setting('app.household_wide', true);
    ids uuid[];
    tid uuid;
    parent_amount numeric;
    n bigint;
    total numeric;
    wrong_sign bigint;
BEGIN
    IF TG_TABLE_NAME = 'transaction' THEN
        ids := ARRAY[NEW.id];
    ELSIF TG_OP = 'INSERT' THEN
        ids := ARRAY[NEW.transaction_id];
    ELSIF TG_OP = 'DELETE' THEN
        ids := ARRAY[OLD.transaction_id];
    ELSE
        ids := ARRAY[NEW.transaction_id, OLD.transaction_id];
    END IF;
    PERFORM set_config('app.household_wide', 'on', true);

    FOREACH tid IN ARRAY ids LOOP
        SELECT t.amount INTO parent_amount FROM public."transaction" t WHERE t.id = tid;
        CONTINUE WHEN NOT FOUND;
        SELECT count(*),
               coalesce(sum(s.amount), 0),
               count(*) FILTER (WHERE sign(s.amount) IS DISTINCT FROM sign(parent_amount))
          INTO n, total, wrong_sign
          FROM public.transaction_split s
         WHERE s.transaction_id = tid;
        CONTINUE WHEN n = 0;
        IF total <> parent_amount THEN
            RAISE EXCEPTION 'splits of transaction % do not sum to its amount', tid
                USING ERRCODE = '23514';
        END IF;
        IF wrong_sign > 0 THEN
            RAISE EXCEPTION 'every split of transaction % must have the sign of its amount', tid
                USING ERRCODE = '23514';
        END IF;
    END LOOP;
    PERFORM set_config('app.household_wide', coalesce(prev_wide, ''), true);
    RETURN NULL;
END
$$;
COMMENT ON FUNCTION transaction_split_balance() IS
    'Deferred check: a transaction''s splits sum exactly to its amount, same sign (ADR 0008).';
CREATE CONSTRAINT TRIGGER transaction_split_balance
    AFTER INSERT OR UPDATE OR DELETE ON transaction_split
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION transaction_split_balance();
CREATE CONSTRAINT TRIGGER transaction_split_balance
    AFTER UPDATE OF amount ON "transaction"
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION transaction_split_balance();
"""


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def _restrictive(name: str, table: str, command: str, using: str | None, check: str | None) -> None:
    sql = f'CREATE POLICY {name} ON "{table}" AS RESTRICTIVE FOR {command}'
    if using is not None:
        sql += f" USING ({using})"
    if check is not None:
        sql += f" WITH CHECK ({check})"
    op.execute(sql)


def upgrade() -> None:
    _create_tables()
    op.execute(HELPER_FUNCTIONS_SQL)

    # household: a household sees only itself. Rows are created by create_household() only, and
    # the app can't delete them either (that will be an owner / SECURITY DEFINER path).
    _enable_rls("household")
    op.execute(
        "CREATE POLICY household_self ON household FOR ALL"
        " USING (id = app_household_id()) WITH CHECK (id = app_household_id())"
    )
    op.execute(f"REVOKE INSERT, DELETE ON household FROM {APP_ROLE}")

    for table in HOUSEHOLD_TABLES:
        _enable_rls(table)
        op.execute(
            f'CREATE POLICY household_isolation ON "{table}" FOR ALL'
            " USING (household_id = app_household_id())"
            " WITH CHECK (household_id = app_household_id())"
        )

    for table, (read, write) in PERSON_POLICIES.items():
        _restrictive("person_read", table, "SELECT", read, None)
        _restrictive("person_insert", table, "INSERT", None, write)
        _restrictive("person_update", table, "UPDATE", write, write)
        _restrictive("person_delete", table, "DELETE", write, None)

    for table, predicate in AGENT_POLICIES.items():
        _restrictive("person_scope", table, "ALL", predicate, predicate)

    op.execute(ACCOUNT_GUARD_SQL)
    op.execute(SPLIT_BALANCE_SQL)

    # audit_log: append-only. Pre-auth rows (household_id NULL) can be written, never read back.
    _enable_rls("audit_log")
    op.execute(
        "CREATE POLICY audit_select ON audit_log FOR SELECT"
        " USING (household_id = app_household_id())"
    )
    op.execute(
        "CREATE POLICY audit_insert ON audit_log FOR INSERT"
        " WITH CHECK (household_id IS NULL OR household_id = app_household_id())"
    )
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {APP_ROLE}")

    for table in REFERENCE_TABLES:
        op.execute(f'REVOKE DELETE, TRUNCATE ON "{table}" FROM {APP_ROLE}')

    op.execute(CREATE_HOUSEHOLD_SQL)
    op.execute(f"GRANT EXECUTE ON FUNCTION create_household(text) TO {APP_ROLE}")


def downgrade() -> None:
    # Dropping the tables drops their policies, triggers and grants. The functions go last
    # because the policies and triggers reference them.
    _drop_tables()
    op.execute("DROP FUNCTION create_household(text)")
    op.execute("DROP FUNCTION transaction_split_balance()")
    op.execute("DROP FUNCTION account_guard_ownership()")
    op.execute("DROP FUNCTION app_household_wide()")
    op.execute("DROP FUNCTION app_person_id()")
    op.execute("DROP FUNCTION app_household_id()")


def _create_tables() -> None:
    # ### commands auto generated by Alembic, reviewed ###
    op.create_table(
        "household",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name=op.f("ck_household_name_length")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_household")),
    )
    op.create_table(
        "institution",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("plaid_institution_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_institution")),
        sa.UniqueConstraint(
            "plaid_institution_id", name=op.f("uq_institution_plaid_institution_id")
        ),
    )
    op.create_table(
        "security",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("plaid_security_id", sa.Text(), nullable=True),
        sa.Column("ticker", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("cusip", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_security")),
        sa.UniqueConstraint("plaid_security_id", name=op.f("uq_security_plaid_security_id")),
    )
    op.create_index(op.f("ix_security_ticker"), "security", ["ticker"], unique=False)
    op.create_table(
        "bank_connection",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=False),
        sa.Column("env", sa.Text(), server_default=sa.text("'sandbox'"), nullable=False),
        sa.Column(
            "products", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("consent_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tx_cursor", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("env IN ('sandbox', 'production')", name=op.f("ck_bank_connection_env")),
        sa.CheckConstraint(
            "provider IN ('plaid', 'simplefin')", name=op.f("ck_bank_connection_provider")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'needs_relink', 'error', 'disconnected')",
            name=op.f("ck_bank_connection_status"),
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_bank_connection_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institution.id"],
            name=op.f("fk_bank_connection_institution_id_institution"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_connection")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_bank_connection_id_household_id")),
        sa.UniqueConstraint(
            "provider", "external_id", name=op.f("uq_bank_connection_provider_external_id")
        ),
    )
    op.create_index(
        op.f("ix_bank_connection_household_id"), "bank_connection", ["household_id"], unique=False
    )
    op.create_index(
        op.f("ix_bank_connection_institution_id"),
        "bank_connection",
        ["institution_id"],
        unique=False,
    )
    op.create_table(
        "budget",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), server_default=sa.text("'monthly'"), nullable=False),
        sa.Column("method", sa.Text(), server_default=sa.text("'category'"), nullable=False),
        sa.Column("rollover", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "method IN ('category', 'envelope', 'zero_based')", name=op.f("ck_budget_method")
        ),
        sa.CheckConstraint(
            "period IN ('monthly', 'biweekly', 'annual')", name=op.f("ck_budget_period")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_budget_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_budget_id_household_id")),
    )
    op.create_index(op.f("ix_budget_household_id"), "budget", ["household_id"], unique=False)
    op.create_table(
        "category",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("is_essential", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("default_tax_tag", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('income', 'expense', 'transfer', 'savings')", name=op.f("ck_category_kind")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_category_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id", "household_id"],
            ["category.id", "category.household_id"],
            name=op.f("fk_category_parent_id_category"),
            ondelete="SET NULL (parent_id)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_category_id_household_id")),
    )
    op.create_index(op.f("ix_category_household_id"), "category", ["household_id"], unique=False)
    op.create_index(op.f("ix_category_parent_id"), "category", ["parent_id"], unique=False)
    op.create_table(
        "household_setting",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value_enc", sa.LargeBinary(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_household_setting_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("household_id", "key", name=op.f("pk_household_setting")),
    )
    op.create_table(
        "person",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("filing_role", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "filing_role IN ('primary', 'spouse', 'dependent')", name=op.f("ck_person_filing_role")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_person_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 200", name=op.f("ck_person_display_name_length")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_person_id_household_id")),
    )
    op.create_index(op.f("ix_person_household_id"), "person", ["household_id"], unique=False)
    op.create_table(
        "agent_conversation",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), server_default=sa.text("'chat'"), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('chat', 'monthly_review', 'year_end')",
            name=op.f("ck_agent_conversation_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_agent_conversation_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_agent_conversation_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_conversation")),
        sa.UniqueConstraint(
            "id", "household_id", name=op.f("uq_agent_conversation_id_household_id")
        ),
    )
    op.create_index(
        op.f("ix_agent_conversation_household_id"),
        "agent_conversation",
        ["household_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_conversation_person_id"), "agent_conversation", ["person_id"], unique=False
    )
    op.create_table(
        "agent_finding",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("tax_year", sa.SmallInteger(), nullable=True),
        sa.Column("severity", sa.Text(), server_default=sa.text("'info'"), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_md", sa.Text(), nullable=False),
        sa.Column(
            "est_savings",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Estimated savings, a non-negative magnitude (never a signed cash flow).",
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'low', 'medium', 'high')", name=op.f("ck_agent_finding_severity")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'accepted', 'dismissed', 'done')",
            name=op.f("ck_agent_finding_status"),
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_agent_finding_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_agent_finding_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "est_savings IS NULL OR est_savings >= 0",
            name=op.f("ck_agent_finding_est_savings_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_finding")),
    )
    op.create_index(
        op.f("ix_agent_finding_household_id"), "agent_finding", ["household_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_finding_person_id"), "agent_finding", ["person_id"], unique=False
    )
    op.create_table(
        "rule",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("100"), nullable=False),
        sa.Column("match", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("set", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.Text(), server_default=sa.text("'user'"), nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("created_by IN ('user', 'agent')", name=op.f("ck_rule_created_by")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_rule_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule")),
    )
    op.create_index(op.f("ix_rule_household_id"), "rule", ["household_id"], unique=False)
    op.create_table(
        "tax_year",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.SmallInteger(), nullable=False),
        sa.Column("filing_status", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column(
            "dependents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "filing_status IS NULL OR filing_status IN ('single', 'mfj', 'mfs', 'hoh', 'qss')",
            name=op.f("ck_tax_year_filing_status"),
        ),
        sa.CheckConstraint("state IS NULL OR state ~ '^[A-Z]{2}$'", name=op.f("ck_tax_year_state")),
        sa.CheckConstraint(
            "status IN ('open', 'prepared', 'filed')", name=op.f("ck_tax_year_status")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_tax_year_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"(dependents::text) IS NULL OR (dependents::text) !~ '{SSN_PATTERN}'",
            name=op.f("ck_tax_year_dependents_no_ssn"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_year")),
        sa.UniqueConstraint("household_id", "year", name=op.f("uq_tax_year_household_id_year")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_tax_year_id_household_id")),
    )
    op.create_index(op.f("ix_tax_year_household_id"), "tax_year", ["household_id"], unique=False)
    op.create_table(
        "account",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("bank_connection_id", sa.Uuid(), nullable=True),
        sa.Column("owner_person_id", sa.Uuid(), nullable=True),
        sa.Column("external_account_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("official_name", sa.Text(), nullable=True),
        sa.Column("mask", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("tax_treatment", sa.Text(), server_default=sa.text("'taxable'"), nullable=False),
        sa.Column("currency", sa.Text(), server_default=sa.text("'USD'"), nullable=False),
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'shared'"), nullable=False),
        sa.Column("opened_at", sa.Date(), nullable=True),
        sa.Column("closed_at", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f("ck_account_currency")),
        sa.CheckConstraint(
            "mask IS NULL OR regexp_replace(mask, '\\D', '', 'g') ~ '^\\d{0,4}$'",
            name=op.f("ck_account_mask_masked"),
        ),
        sa.CheckConstraint(
            "source IN ('plaid', 'simplefin', 'import', 'manual')", name=op.f("ck_account_source")
        ),
        sa.CheckConstraint(
            "tax_treatment IN ('taxable', 'traditional', 'roth', 'hsa')",
            name=op.f("ck_account_tax_treatment"),
        ),
        sa.CheckConstraint(
            "type IN ('depository', 'credit', 'loan', 'investment', 'other')",
            name=op.f("ck_account_type"),
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'shared', 'summary_only')",
            name=op.f("ck_account_visibility"),
        ),
        sa.ForeignKeyConstraint(
            ["bank_connection_id", "household_id"],
            ["bank_connection.id", "bank_connection.household_id"],
            name=op.f("fk_account_bank_connection_id_bank_connection"),
            ondelete="SET NULL (bank_connection_id)",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_account_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_account_owner_person_id_person"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
        sa.UniqueConstraint(
            "bank_connection_id",
            "external_account_id",
            name=op.f("uq_account_bank_connection_id_external_account_id"),
        ),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_account_id_household_id")),
    )
    op.create_index(
        op.f("ix_account_bank_connection_id"), "account", ["bank_connection_id"], unique=False
    )
    op.create_index(op.f("ix_account_household_id"), "account", ["household_id"], unique=False)
    op.create_index(
        op.f("ix_account_owner_person_id"), "account", ["owner_person_id"], unique=False
    )
    op.create_table(
        "agent_message",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('system', 'user', 'assistant', 'tool')", name=op.f("ck_agent_message_role")
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "household_id"],
            ["agent_conversation.id", "agent_conversation.household_id"],
            name=op.f("fk_agent_message_conversation_id_agent_conversation"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_agent_message_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_message")),
    )
    op.create_index(
        op.f("ix_agent_message_conversation_id"), "agent_message", ["conversation_id"], unique=False
    )
    op.create_index(
        op.f("ix_agent_message_household_id"), "agent_message", ["household_id"], unique=False
    )
    op.create_table(
        "app_user",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("totp_secret_enc", sa.LargeBinary(), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("role", sa.Text(), server_default=sa.text("'member'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('owner', 'member')", name=op.f("ck_app_user_role")),
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_app_user_email_lowercase")),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_app_user_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_user")),
        sa.UniqueConstraint("email", name=op.f("uq_app_user_email")),
        sa.UniqueConstraint("person_id", name=op.f("uq_app_user_person_id")),
    )
    op.create_table(
        "budget_line",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("budget_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Planned amount, non-negative magnitude.",
        ),
        sa.Column(
            "alert_pct",
            sa.Numeric(precision=5, scale=4),
            server_default=sa.text("0.8"),
            nullable=False,
            comment="Fraction in [0, 1]: 0.8000 alerts at 80%.",
        ),
        sa.ForeignKeyConstraint(
            ["budget_id", "household_id"],
            ["budget.id", "budget.household_id"],
            name=op.f("fk_budget_line_budget_id_budget"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id", "household_id"],
            ["category.id", "category.household_id"],
            name=op.f("fk_budget_line_category_id_category"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_budget_line_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0", name=op.f("ck_budget_line_amount_non_negative")
        ),
        sa.CheckConstraint(
            "alert_pct IS NULL OR alert_pct BETWEEN 0 AND 1",
            name=op.f("ck_budget_line_alert_pct_ratio"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_line")),
        sa.UniqueConstraint(
            "budget_id", "category_id", name=op.f("uq_budget_line_budget_id_category_id")
        ),
    )
    op.create_index(
        op.f("ix_budget_line_category_id"), "budget_line", ["category_id"], unique=False
    )
    op.create_index(
        op.f("ix_budget_line_household_id"), "budget_line", ["household_id"], unique=False
    )
    op.create_table(
        "credit_score_entry",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("bureau", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "bureau IS NULL OR bureau IN ('equifax', 'experian', 'transunion')",
            name=op.f("ck_credit_score_entry_bureau"),
        ),
        sa.CheckConstraint(
            "score BETWEEN 250 AND 990", name=op.f("ck_credit_score_entry_score_range")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_credit_score_entry_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_credit_score_entry_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_score_entry")),
    )
    op.create_index(
        op.f("ix_credit_score_entry_household_id"),
        "credit_score_entry",
        ["household_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_credit_score_entry_person_id"), "credit_score_entry", ["person_id"], unique=False
    )
    op.create_table(
        "document",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("tax_year", sa.SmallInteger(), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("mime", sa.Text(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("parse_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('statement', 'w2', '1099-int', '1099-div', '1099-b', '1099-r',"
            " '1099-nec', '1099-misc', '1099-g', '1099-k', '1099-sa', '1098', '1098-e',"
            " '1098-t', '5498', '5498-sa', '1095-a', 'k-1', 'credit_report', 'receipt',"
            " 'export', 'other')",
            name=op.f("ck_document_kind"),
        ),
        sa.CheckConstraint(
            "parse_status IN ('pending', 'parsed', 'needs_review', 'failed')",
            name=op.f("ck_document_parse_status"),
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_sha256_hex")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_document_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_document_person_id_person"),
            ondelete="SET NULL (person_id)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document")),
        sa.UniqueConstraint("household_id", "sha256", name=op.f("uq_document_household_id_sha256")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_document_id_household_id")),
    )
    op.create_index(op.f("ix_document_household_id"), "document", ["household_id"], unique=False)
    op.create_index(op.f("ix_document_person_id"), "document", ["person_id"], unique=False)
    op.create_table(
        "estimate_snapshot",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("tax_year_id", sa.Uuid(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column(
            "federal_liability",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Non-negative magnitude.",
        ),
        sa.Column(
            "state_liability",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Non-negative magnitude.",
        ),
        sa.Column(
            "withheld",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Non-negative magnitude.",
        ),
        sa.Column(
            "projected_due",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Positive = owed at filing, negative = expected refund.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_estimate_snapshot_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tax_year_id", "household_id"],
            ["tax_year.id", "tax_year.household_id"],
            name=op.f("fk_estimate_snapshot_tax_year_id_tax_year"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state_liability IS NULL OR state_liability >= 0",
            name=op.f("ck_estimate_snapshot_state_liability_non_negative"),
        ),
        sa.CheckConstraint(
            "withheld IS NULL OR withheld >= 0",
            name=op.f("ck_estimate_snapshot_withheld_non_negative"),
        ),
        sa.CheckConstraint(
            "federal_liability IS NULL OR federal_liability >= 0",
            name=op.f("ck_estimate_snapshot_federal_liability_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_estimate_snapshot")),
    )
    op.create_index(
        op.f("ix_estimate_snapshot_household_id"),
        "estimate_snapshot",
        ["household_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_estimate_snapshot_tax_year_id"), "estimate_snapshot", ["tax_year_id"], unique=False
    )
    op.create_table(
        "tax_item",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("tax_year_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("form", sa.Text(), nullable=False),
        sa.Column("line_key", sa.Text(), nullable=False),
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Signed as on the form line (losses negative), not the cash-flow convention.",
        ),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column(
            "confidence",
            sa.Numeric(precision=5, scale=4),
            nullable=True,
            comment="Fraction in [0, 1].",
        ),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('document', 'transaction', 'manual', 'agent')",
            name=op.f("ck_tax_item_source_type"),
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_tax_item_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_tax_item_person_id_person"),
            ondelete="SET NULL (person_id)",
        ),
        sa.ForeignKeyConstraint(
            ["tax_year_id", "household_id"],
            ["tax_year.id", "tax_year.household_id"],
            name=op.f("fk_tax_item_tax_year_id_tax_year"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name=op.f("ck_tax_item_confidence_ratio"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_item")),
    )
    op.create_index(op.f("ix_tax_item_household_id"), "tax_item", ["household_id"], unique=False)
    op.create_index(op.f("ix_tax_item_person_id"), "tax_item", ["person_id"], unique=False)
    op.create_index(op.f("ix_tax_item_tax_year_id"), "tax_item", ["tax_year_id"], unique=False)
    op.create_table(
        "tax_scenario",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("tax_year_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by", sa.Text(), server_default=sa.text("'user'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "created_by IN ('user', 'agent')", name=op.f("ck_tax_scenario_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_tax_scenario_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tax_year_id", "household_id"],
            ["tax_year.id", "tax_year.household_id"],
            name=op.f("fk_tax_scenario_tax_year_id_tax_year"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_scenario")),
    )
    op.create_index(
        op.f("ix_tax_scenario_household_id"), "tax_scenario", ["household_id"], unique=False
    )
    op.create_index(
        op.f("ix_tax_scenario_tax_year_id"), "tax_scenario", ["tax_year_id"], unique=False
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        "ix_audit_log_household_id_at", "audit_log", ["household_id", "at"], unique=False
    )
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"], unique=False)
    op.create_table(
        "auth_session",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reauth_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_auth_session_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auth_session")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auth_session_token_hash")),
    )
    op.create_index(op.f("ix_auth_session_user_id"), "auth_session", ["user_id"], unique=False)
    op.create_table(
        "balance_snapshot",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column(
            "current",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Assets positive; credit/loan negative = owed (Plaid negated on ingest).",
        ),
        sa.Column(
            "available",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Spendable / drawable now, as reported (positive; remaining credit for cards).",
        ),
        sa.Column(
            "limit_amt",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Credit limit, positive magnitude.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_balance_snapshot_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_balance_snapshot_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "limit_amt IS NULL OR limit_amt >= 0",
            name=op.f("ck_balance_snapshot_limit_amt_non_negative"),
        ),
        sa.PrimaryKeyConstraint("account_id", "as_of_date", name=op.f("pk_balance_snapshot")),
    )
    op.create_index(
        "ix_balance_snapshot_as_of_date", "balance_snapshot", ["as_of_date"], unique=False
    )
    op.create_index(
        op.f("ix_balance_snapshot_household_id"), "balance_snapshot", ["household_id"], unique=False
    )
    op.create_table(
        "credit_report",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("bureau", sa.Text(), nullable=False),
        sa.Column("pulled_on", sa.Date(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("parsed_ok", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "bureau IN ('equifax', 'experian', 'transunion')", name=op.f("ck_credit_report_bureau")
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "household_id"],
            ["document.id", "document.household_id"],
            name=op.f("fk_credit_report_document_id_document"),
            ondelete="SET NULL (document_id)",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_credit_report_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_credit_report_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_report")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_credit_report_id_household_id")),
    )
    op.create_index(
        op.f("ix_credit_report_document_id"), "credit_report", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_credit_report_household_id"), "credit_report", ["household_id"], unique=False
    )
    op.create_index(
        op.f("ix_credit_report_person_id"), "credit_report", ["person_id"], unique=False
    )
    op.create_table(
        "holding",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("security_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=28, scale=10), nullable=False),
        sa.Column("cost_basis", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("value", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_holding_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_holding_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["security.id"],
            name=op.f("fk_holding_security_id_security"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holding")),
        sa.UniqueConstraint(
            "account_id",
            "security_id",
            "as_of_date",
            name=op.f("uq_holding_account_id_security_id_as_of_date"),
        ),
    )
    op.create_index(op.f("ix_holding_household_id"), "holding", ["household_id"], unique=False)
    op.create_index(op.f("ix_holding_security_id"), "holding", ["security_id"], unique=False)
    op.create_table(
        "investment_txn",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("subtype", sa.Text(), nullable=True),
        sa.Column("security_id", sa.Uuid(), nullable=True),
        sa.Column(
            "quantity",
            sa.Numeric(precision=28, scale=10),
            nullable=True,
            comment="Change in units held: buy positive, sell negative.",
        ),
        sa.Column(
            "price",
            sa.Numeric(precision=18, scale=8),
            nullable=True,
            comment="Per-unit price, non-negative.",
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Cash effect: buy negative (outflow), sell/dividend positive. Plaid negated.",
        ),
        sa.Column(
            "fees",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Fees as a non-negative magnitude, already included in amount.",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "type IN ('buy', 'sell', 'cancel', 'cash', 'fee', 'transfer')",
            name=op.f("ck_investment_txn_type"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_investment_txn_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_investment_txn_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["security.id"],
            name=op.f("fk_investment_txn_security_id_security"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "price IS NULL OR price >= 0", name=op.f("ck_investment_txn_price_non_negative")
        ),
        sa.CheckConstraint(
            "fees IS NULL OR fees >= 0", name=op.f("ck_investment_txn_fees_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_investment_txn")),
        sa.UniqueConstraint(
            "account_id", "external_id", name=op.f("uq_investment_txn_account_id_external_id")
        ),
    )
    op.create_index(
        op.f("ix_investment_txn_household_id"), "investment_txn", ["household_id"], unique=False
    )
    op.create_index(
        op.f("ix_investment_txn_security_id"), "investment_txn", ["security_id"], unique=False
    )
    op.create_table(
        "liability",
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "apr",
            sa.Numeric(precision=7, scale=4),
            nullable=True,
            comment="Annual percentage rate as a percent: 24.9900 = 24.99% (not a fraction).",
        ),
        sa.Column(
            "min_payment",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Non-negative magnitude.",
        ),
        sa.Column("next_due_date", sa.Date(), nullable=True),
        sa.Column(
            "statement_balance",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="As stated by the lender: positive = owed, negative = credit balance.",
        ),
        sa.Column(
            "original_principal",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Non-negative magnitude.",
        ),
        sa.Column("payoff_date", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('credit', 'student', 'mortgage', 'auto', 'personal', 'other')",
            name=op.f("ck_liability_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_liability_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_liability_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("apr IS NULL OR apr >= 0", name=op.f("ck_liability_apr_non_negative")),
        sa.CheckConstraint(
            "min_payment IS NULL OR min_payment >= 0",
            name=op.f("ck_liability_min_payment_non_negative"),
        ),
        sa.CheckConstraint(
            "original_principal IS NULL OR original_principal >= 0",
            name=op.f("ck_liability_original_principal_non_negative"),
        ),
        sa.PrimaryKeyConstraint("account_id", name=op.f("pk_liability")),
    )
    op.create_index(op.f("ix_liability_household_id"), "liability", ["household_id"], unique=False)
    op.create_table(
        "parsed_field",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "confidence",
            sa.Numeric(precision=5, scale=4),
            nullable=True,
            comment="Fraction in [0, 1].",
        ),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("bbox", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "verified_by_user", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "household_id"],
            ["document.id", "document.household_id"],
            name=op.f("fk_parsed_field_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_parsed_field_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name=op.f("ck_parsed_field_confidence_ratio"),
        ),
        sa.CheckConstraint(
            f"value IS NULL OR value !~ '{SSN_PATTERN}'",
            name=op.f("ck_parsed_field_value_no_ssn"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parsed_field")),
    )
    op.create_index(
        op.f("ix_parsed_field_document_id"), "parsed_field", ["document_id"], unique=False
    )
    op.create_index(
        op.f("ix_parsed_field_household_id"), "parsed_field", ["household_id"], unique=False
    )
    op.create_table(
        "totp_recovery_code",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_totp_recovery_code_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_totp_recovery_code")),
    )
    op.create_index(
        op.f("ix_totp_recovery_code_user_id"), "totp_recovery_code", ["user_id"], unique=False
    )
    op.create_table(
        "transaction",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("import_fingerprint", sa.Text(), nullable=True),
        sa.Column("posted_date", sa.Date(), nullable=False),
        sa.Column("authorized_date", sa.Date(), nullable=True),
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Outflow negative, inflow positive. Plaid amounts are negated on ingest.",
        ),
        sa.Column("description_raw", sa.Text(), nullable=False),
        sa.Column("merchant_name", sa.Text(), nullable=True),
        sa.Column("plaid_pfc_primary", sa.Text(), nullable=True),
        sa.Column("plaid_pfc_detailed", sa.Text(), nullable=True),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column("category_source", sa.Text(), nullable=True),
        sa.Column("is_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_transfer", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("transfer_group_id", sa.Uuid(), nullable=True),
        sa.Column("is_tax_relevant", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("tax_tag", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("receipt_document_id", sa.Uuid(), nullable=True),
        sa.Column(
            "pending_external_id",
            sa.Text(),
            nullable=True,
            comment="Plaid pending_transaction_id: external_id of the pending row replaced.",
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "category_source IS NULL OR category_source IN ('rule', 'plaid', 'ml', 'user')",
            name=op.f("ck_transaction_category_source"),
        ),
        sa.CheckConstraint(
            "source IN ('plaid', 'simplefin', 'import', 'manual')",
            name=op.f("ck_transaction_source"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_transaction_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["category_id", "household_id"],
            ["category.id", "category.household_id"],
            name=op.f("fk_transaction_category_id_category"),
            ondelete="SET NULL (category_id)",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_transaction_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_document_id", "household_id"],
            ["document.id", "document.household_id"],
            name=op.f("fk_transaction_receipt_document_id_document"),
            ondelete="SET NULL (receipt_document_id)",
        ),
        sa.CheckConstraint(
            "transfer_group_id IS NULL OR is_transfer", name=op.f("ck_transaction_transfer_group")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction")),
        sa.UniqueConstraint(
            "account_id", "external_id", name=op.f("uq_transaction_account_id_external_id")
        ),
        sa.UniqueConstraint(
            "account_id",
            "import_fingerprint",
            name=op.f("uq_transaction_account_id_import_fingerprint"),
        ),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_transaction_id_household_id")),
    )
    op.create_index(
        "ix_transaction_account_id_posted_date",
        "transaction",
        ["account_id", "posted_date"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_category_id_posted_date",
        "transaction",
        ["category_id", "posted_date"],
        unique=False,
    )
    op.create_index(
        "ix_transaction_description_raw_trgm",
        "transaction",
        ["description_raw"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"description_raw": "gin_trgm_ops"},
    )
    op.create_index(
        op.f("ix_transaction_household_id"), "transaction", ["household_id"], unique=False
    )
    op.create_index("ix_transaction_merchant_name", "transaction", ["merchant_name"], unique=False)
    op.create_index(
        op.f("ix_transaction_pending_external_id"),
        "transaction",
        ["pending_external_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transaction_receipt_document_id"),
        "transaction",
        ["receipt_document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transaction_transfer_group_id"), "transaction", ["transfer_group_id"], unique=False
    )
    op.create_table(
        "webauthn_credential",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "transports",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("aaguid", sa.Uuid(), nullable=True),
        sa.Column("backup_eligible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("backup_state", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("nickname", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name=op.f("fk_webauthn_credential_user_id_app_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webauthn_credential")),
        sa.UniqueConstraint("credential_id", name=op.f("uq_webauthn_credential_credential_id")),
    )
    op.create_index(
        op.f("ix_webauthn_credential_user_id"), "webauthn_credential", ["user_id"], unique=False
    )
    op.create_table(
        "credit_inquiry",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("credit_report_id", sa.Uuid(), nullable=False),
        sa.Column("creditor", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.CheckConstraint("kind IN ('hard', 'soft')", name=op.f("ck_credit_inquiry_kind")),
        sa.ForeignKeyConstraint(
            ["credit_report_id", "household_id"],
            ["credit_report.id", "credit_report.household_id"],
            name=op.f("fk_credit_inquiry_credit_report_id_credit_report"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_credit_inquiry_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credit_inquiry")),
    )
    op.create_index(
        op.f("ix_credit_inquiry_credit_report_id"),
        "credit_inquiry",
        ["credit_report_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_credit_inquiry_household_id"), "credit_inquiry", ["household_id"], unique=False
    )
    op.create_table(
        "tradeline",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("credit_report_id", sa.Uuid(), nullable=False),
        sa.Column("creditor", sa.Text(), nullable=False),
        sa.Column("account_type", sa.Text(), nullable=True),
        sa.Column("opened", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column(
            "credit_limit",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="As reported, non-negative.",
        ),
        sa.Column(
            "balance",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Amount owed as reported by the bureau, non-negative magnitude.",
        ),
        sa.Column(
            "high_balance",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Highest balance as reported, non-negative magnitude.",
        ),
        sa.Column("payment_history", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("account_number_masked", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "account_number_masked IS NULL"
            " OR regexp_replace(account_number_masked, '\\D', '', 'g') ~ '^\\d{0,4}$'",
            name=op.f("ck_tradeline_account_number_masked_masked"),
        ),
        sa.ForeignKeyConstraint(
            ["credit_report_id", "household_id"],
            ["credit_report.id", "credit_report.household_id"],
            name=op.f("fk_tradeline_credit_report_id_credit_report"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_tradeline_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "balance IS NULL OR balance >= 0", name=op.f("ck_tradeline_balance_non_negative")
        ),
        sa.CheckConstraint(
            "high_balance IS NULL OR high_balance >= 0",
            name=op.f("ck_tradeline_high_balance_non_negative"),
        ),
        sa.CheckConstraint(
            "credit_limit IS NULL OR credit_limit >= 0",
            name=op.f("ck_tradeline_credit_limit_non_negative"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tradeline")),
    )
    op.create_index(
        op.f("ix_tradeline_credit_report_id"), "tradeline", ["credit_report_id"], unique=False
    )
    op.create_index(op.f("ix_tradeline_household_id"), "tradeline", ["household_id"], unique=False)
    op.create_table(
        "transaction_split",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=True),
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            nullable=False,
            comment="Same sign as the parent; a transaction's splits sum to its amount.",
        ),
        sa.Column("tax_tag", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["category_id", "household_id"],
            ["category.id", "category.household_id"],
            name=op.f("fk_transaction_split_category_id_category"),
            ondelete="SET NULL (category_id)",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_transaction_split_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id", "household_id"],
            ["transaction.id", "transaction.household_id"],
            name=op.f("fk_transaction_split_transaction_id_transaction"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transaction_split")),
    )
    op.create_index(
        op.f("ix_transaction_split_category_id"), "transaction_split", ["category_id"], unique=False
    )
    op.create_index(
        op.f("ix_transaction_split_household_id"),
        "transaction_split",
        ["household_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_transaction_split_transaction_id"),
        "transaction_split",
        ["transaction_id"],
        unique=False,
    )
    # ### end Alembic commands ###


def _drop_tables() -> None:
    # ### commands auto generated by Alembic, reviewed ###
    op.drop_index(op.f("ix_transaction_split_transaction_id"), table_name="transaction_split")
    op.drop_index(op.f("ix_transaction_split_household_id"), table_name="transaction_split")
    op.drop_index(op.f("ix_transaction_split_category_id"), table_name="transaction_split")
    op.drop_table("transaction_split")
    op.drop_index(op.f("ix_tradeline_household_id"), table_name="tradeline")
    op.drop_index(op.f("ix_tradeline_credit_report_id"), table_name="tradeline")
    op.drop_table("tradeline")
    op.drop_index(op.f("ix_credit_inquiry_household_id"), table_name="credit_inquiry")
    op.drop_index(op.f("ix_credit_inquiry_credit_report_id"), table_name="credit_inquiry")
    op.drop_table("credit_inquiry")
    op.drop_index(op.f("ix_webauthn_credential_user_id"), table_name="webauthn_credential")
    op.drop_table("webauthn_credential")
    op.drop_index(op.f("ix_transaction_transfer_group_id"), table_name="transaction")
    op.drop_index(op.f("ix_transaction_receipt_document_id"), table_name="transaction")
    op.drop_index(op.f("ix_transaction_pending_external_id"), table_name="transaction")
    op.drop_index("ix_transaction_merchant_name", table_name="transaction")
    op.drop_index(op.f("ix_transaction_household_id"), table_name="transaction")
    op.drop_index(
        "ix_transaction_description_raw_trgm",
        table_name="transaction",
        postgresql_using="gin",
        postgresql_ops={"description_raw": "gin_trgm_ops"},
    )
    op.drop_index("ix_transaction_category_id_posted_date", table_name="transaction")
    op.drop_index("ix_transaction_account_id_posted_date", table_name="transaction")
    op.drop_table("transaction")
    op.drop_index(op.f("ix_totp_recovery_code_user_id"), table_name="totp_recovery_code")
    op.drop_table("totp_recovery_code")
    op.drop_index(op.f("ix_parsed_field_household_id"), table_name="parsed_field")
    op.drop_index(op.f("ix_parsed_field_document_id"), table_name="parsed_field")
    op.drop_table("parsed_field")
    op.drop_index(op.f("ix_liability_household_id"), table_name="liability")
    op.drop_table("liability")
    op.drop_index(op.f("ix_investment_txn_security_id"), table_name="investment_txn")
    op.drop_index(op.f("ix_investment_txn_household_id"), table_name="investment_txn")
    op.drop_table("investment_txn")
    op.drop_index(op.f("ix_holding_security_id"), table_name="holding")
    op.drop_index(op.f("ix_holding_household_id"), table_name="holding")
    op.drop_table("holding")
    op.drop_index(op.f("ix_credit_report_person_id"), table_name="credit_report")
    op.drop_index(op.f("ix_credit_report_household_id"), table_name="credit_report")
    op.drop_index(op.f("ix_credit_report_document_id"), table_name="credit_report")
    op.drop_table("credit_report")
    op.drop_index(op.f("ix_balance_snapshot_household_id"), table_name="balance_snapshot")
    op.drop_index("ix_balance_snapshot_as_of_date", table_name="balance_snapshot")
    op.drop_table("balance_snapshot")
    op.drop_index(op.f("ix_auth_session_user_id"), table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_index("ix_audit_log_household_id_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(op.f("ix_tax_scenario_tax_year_id"), table_name="tax_scenario")
    op.drop_index(op.f("ix_tax_scenario_household_id"), table_name="tax_scenario")
    op.drop_table("tax_scenario")
    op.drop_index(op.f("ix_tax_item_tax_year_id"), table_name="tax_item")
    op.drop_index(op.f("ix_tax_item_person_id"), table_name="tax_item")
    op.drop_index(op.f("ix_tax_item_household_id"), table_name="tax_item")
    op.drop_table("tax_item")
    op.drop_index(op.f("ix_estimate_snapshot_tax_year_id"), table_name="estimate_snapshot")
    op.drop_index(op.f("ix_estimate_snapshot_household_id"), table_name="estimate_snapshot")
    op.drop_table("estimate_snapshot")
    op.drop_index(op.f("ix_document_person_id"), table_name="document")
    op.drop_index(op.f("ix_document_household_id"), table_name="document")
    op.drop_table("document")
    op.drop_index(op.f("ix_credit_score_entry_person_id"), table_name="credit_score_entry")
    op.drop_index(op.f("ix_credit_score_entry_household_id"), table_name="credit_score_entry")
    op.drop_table("credit_score_entry")
    op.drop_index(op.f("ix_budget_line_household_id"), table_name="budget_line")
    op.drop_index(op.f("ix_budget_line_category_id"), table_name="budget_line")
    op.drop_table("budget_line")
    op.drop_table("app_user")
    op.drop_index(op.f("ix_agent_message_household_id"), table_name="agent_message")
    op.drop_index(op.f("ix_agent_message_conversation_id"), table_name="agent_message")
    op.drop_table("agent_message")
    op.drop_index(op.f("ix_agent_finding_person_id"), table_name="agent_finding")
    op.drop_index(op.f("ix_agent_finding_household_id"), table_name="agent_finding")
    op.drop_table("agent_finding")
    op.drop_index(op.f("ix_agent_conversation_person_id"), table_name="agent_conversation")
    op.drop_index(op.f("ix_agent_conversation_household_id"), table_name="agent_conversation")
    op.drop_table("agent_conversation")
    op.drop_index(op.f("ix_account_owner_person_id"), table_name="account")
    op.drop_index(op.f("ix_account_household_id"), table_name="account")
    op.drop_index(op.f("ix_account_bank_connection_id"), table_name="account")
    op.drop_table("account")
    op.drop_index(op.f("ix_tax_year_household_id"), table_name="tax_year")
    op.drop_table("tax_year")
    op.drop_index(op.f("ix_rule_household_id"), table_name="rule")
    op.drop_table("rule")
    op.drop_index(op.f("ix_person_household_id"), table_name="person")
    op.drop_table("person")
    op.drop_table("household_setting")
    op.drop_index(op.f("ix_category_parent_id"), table_name="category")
    op.drop_index(op.f("ix_category_household_id"), table_name="category")
    op.drop_table("category")
    op.drop_index(op.f("ix_budget_household_id"), table_name="budget")
    op.drop_table("budget")
    op.drop_index(op.f("ix_bank_connection_institution_id"), table_name="bank_connection")
    op.drop_index(op.f("ix_bank_connection_household_id"), table_name="bank_connection")
    op.drop_table("bank_connection")
    op.drop_index(op.f("ix_security_ticker"), table_name="security")
    op.drop_table("security")
    op.drop_table("institution")
    op.drop_table("household")
    # ### end Alembic commands ###
