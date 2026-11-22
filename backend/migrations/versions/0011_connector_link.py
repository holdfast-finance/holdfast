"""Connector link schema: slot ledger, link decisions, statement key, coverage (Phase 2 P4a).

The connector lane's one migration (contract §4, ADRs 0033, 0034, 0035). Tables come from the
models (reviewed by hand); everything autogenerate can't see is written out here:

- ``plaid_link_slot`` (ADR 0034 §1): ENABLE + FORCE RLS and ``household_isolation``. The app role
  loses DELETE (and has no TRUNCATE): the ledger only grows. ``plaid_link_slot_no_delete``
  refuses a DELETE while the row's household still exists, so only a household cascade removes
  rows. ``plaid_link_slot_guard``: ``reserved`` may move to any other state, ``unknown`` only to
  ``abandoned``; the client, env, owner count, creator, household and creation time are final,
  and a recorded ``item_id`` never changes. ``plaid_slot_count(client_fp, env)`` is SECURITY
  DEFINER and returns a number only, across households (#31's ``list_household_ids`` pattern: a
  SELECT-only policy ``TO holdfast_owner``, ``search_path`` pinned, EXECUTE for holdfast_app).
- ``connector_account_link`` (ADR 0033 §1): ENABLE + FORCE RLS and ``household_isolation``.
- ``account_coverage`` (ADR 0035 §1): ENABLE + FORCE RLS, ``household_isolation`` and the
  RESTRICTIVE person policies of the account detail tables (0002): read needs the account's
  details visible, insert / update / delete need the account writable or a household-wide
  session.
- ``account.statement_key``: backfilled from ``external_account_id`` on imported accounts, and
  the per-household unique key moves from ``(household_id, external_account_id) WHERE source =
  'import'`` to ``(household_id, statement_key)``, so the key survives adoption.
  ``account.adopted_at`` and ``account_guard_source`` (ADR 0033 §6): ``source`` changes only
  ``import`` -> ``plaid``/``simplefin`` (with a production connection of that provider and
  ``adopted_at`` in the same UPDATE) and back (un-adopt: no connector rows left, connection and
  ``adopted_at`` cleared, ``external_account_id`` back to the statement key). A statement key is
  final once set.
- ``bank_connection.client_fp`` and status ``pending_setup``.
  ``balance_snapshot.bank_connection_id`` (``SET NULL``) marks connector-written snapshots, so
  un-adopt deletes only those (Bulwark, #62).
- ``sync_run``: ``rows_superseded``, ``rows_skipped_covered``, ``rows_held``,
  ``history_complete``. ``job_run.requested_by`` (person, ``SET NULL``).

Grants: new tables get the owner's default privileges (SELECT/INSERT/UPDATE/DELETE for
holdfast_app, no DDL); only ``plaid_link_slot`` is narrowed.

Downgrade reverses everything. It is lossy by nature: ``pending_setup`` connections become
``disconnected`` (nothing was ingested from them, and every scheduled job skips both).

Revision ID: 0011_connector_link
Revises: 0010_list_household_ids
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_connector_link"
down_revision: str | None = "0010_list_household_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "holdfast_app"
OWNER_ROLE = "holdfast_owner"

# Copied from app.models._common (migrations never import app code).
CLIENT_FP_PATTERN = "^[0-9a-f]{16}$"
CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
OLD_STATUSES = "'active', 'needs_relink', 'error', 'disconnected'"
NEW_STATUSES = f"{OLD_STATUSES}, 'pending_setup'"
OLD_SYNC_COUNTS = "rows_added >= 0 AND rows_modified >= 0 AND rows_removed >= 0 AND pages >= 0"
NEW_SYNC_COUNTS = (
    f"{OLD_SYNC_COUNTS} AND rows_superseded >= 0 AND rows_skipped_covered >= 0 AND rows_held >= 0"
)

# Same predicates as 0002's ACCOUNT_DETAIL_VISIBLE / ACCOUNT_DETAIL_WRITABLE.
COVERAGE_READ = (
    "EXISTS (SELECT 1 FROM account a WHERE a.id = account_coverage.account_id AND ("
    "a.visibility = 'shared' OR a.owner_person_id IS NULL"
    " OR a.owner_person_id = app_person_id() OR app_household_wide()))"
)
COVERAGE_WRITE = (
    "app_household_wide() OR EXISTS (SELECT 1 FROM account a"
    " WHERE a.id = account_coverage.account_id"
    " AND (a.owner_person_id IS NULL OR a.owner_person_id = app_person_id()))"
)


def _code_check(column: str) -> str:
    return f"{column} IS NULL OR (length({column}) <= 64 AND {column} ~ '{CODE_PATTERN}')"


# Counting connector rows runs household-wide (then restores the caller's value), so a
# person-scoped policy can never hide one and fail open (0005's pattern). The connection lookup
# needs no widening: bank_connection is household-visible. Messages carry ids and source names.
ACCOUNT_SOURCE_GUARD_SQL = """
CREATE FUNCTION account_guard_source() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    prev_wide text := current_setting('app.household_wide', true);
    conn_env text;
    conn_provider text;
    has_connector_rows boolean;
BEGIN
    IF OLD.statement_key IS NOT NULL
       AND NEW.statement_key IS DISTINCT FROM OLD.statement_key THEN
        RAISE EXCEPTION 'the statement key of account % is final', OLD.id
            USING ERRCODE = '23514';
    END IF;
    IF NEW.source IS NOT DISTINCT FROM OLD.source THEN
        IF NEW.adopted_at IS DISTINCT FROM OLD.adopted_at THEN
            RAISE EXCEPTION 'account %: adopted_at changes only with adoption', OLD.id
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.source = 'import' AND NEW.source IN ('plaid', 'simplefin') THEN
        IF NEW.bank_connection_id IS NULL OR NEW.adopted_at IS NULL THEN
            RAISE EXCEPTION 'adopting account % needs its connection and adopted_at', OLD.id
                USING ERRCODE = '23514';
        END IF;
        SELECT c.env, c.provider INTO conn_env, conn_provider
          FROM public.bank_connection c
         WHERE c.id = NEW.bank_connection_id AND c.household_id = NEW.household_id;
        IF conn_env IS DISTINCT FROM 'production' OR conn_provider IS DISTINCT FROM NEW.source THEN
            RAISE EXCEPTION
                'account % can be adopted only by a production % connection', OLD.id, NEW.source
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.source IN ('plaid', 'simplefin') AND NEW.source = 'import' THEN
        IF OLD.adopted_at IS NULL
           OR NEW.adopted_at IS NOT NULL
           OR NEW.bank_connection_id IS NOT NULL
           OR NEW.statement_key IS NULL
           OR NEW.external_account_id IS DISTINCT FROM NEW.statement_key THEN
            RAISE EXCEPTION 'account %: un-adopt clears connection and adoption, restores key',
                OLD.id
                USING ERRCODE = '23514';
        END IF;
        PERFORM set_config('app.household_wide', 'on', true);
        SELECT EXISTS (
            SELECT 1 FROM public."transaction" t
             WHERE t.account_id = OLD.id AND t.source IN ('plaid', 'simplefin')
        ) INTO has_connector_rows;
        PERFORM set_config('app.household_wide', coalesce(prev_wide, ''), true);
        IF has_connector_rows THEN
            RAISE EXCEPTION 'account % still holds connector rows', OLD.id
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'the source of account % cannot change from % to %',
        OLD.id, OLD.source, NEW.source
        USING ERRCODE = '23514';
END
$$;
COMMENT ON FUNCTION account_guard_source() IS
    'Adoption and un-adopt are the only source changes on an account (ADR 0033 section 6).';
CREATE TRIGGER account_guard_source
    BEFORE UPDATE ON account
    FOR EACH ROW EXECUTE FUNCTION account_guard_source();
"""

SLOT_GUARD_SQL = """
CREATE FUNCTION plaid_link_slot_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF NEW.household_id IS DISTINCT FROM OLD.household_id
       OR NEW.client_fp IS DISTINCT FROM OLD.client_fp
       OR NEW.env IS DISTINCT FROM OLD.env
       OR NEW.owner_count IS DISTINCT FROM OLD.owner_count
       OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (OLD.item_id IS NOT NULL AND NEW.item_id IS DISTINCT FROM OLD.item_id) THEN
        RAISE EXCEPTION 'slot % is final apart from its resolution', OLD.id
            USING ERRCODE = '23514';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state
       AND NOT (OLD.state = 'reserved'
                OR (OLD.state = 'unknown' AND NEW.state = 'abandoned')) THEN
        RAISE EXCEPTION 'slot %: % cannot become %', OLD.id, OLD.state, NEW.state
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION plaid_link_slot_guard() IS
    'Slot ledger transitions: reserved -> any, unknown -> abandoned; the rest is final (ADR 0034).';
CREATE TRIGGER plaid_link_slot_guard
    BEFORE UPDATE ON plaid_link_slot
    FOR EACH ROW EXECUTE FUNCTION plaid_link_slot_guard();
"""

# The app role has no DELETE grant; this binds the owner too. SECURITY DEFINER so the household
# check sees every household through the owner-only SELECT policy (0010): while the household
# exists the DELETE is refused, and inside its cascade the household row is already gone.
SLOT_NO_DELETE_SQL = """
CREATE FUNCTION plaid_link_slot_no_delete() RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM public.household h WHERE h.id = OLD.household_id) THEN
        RAISE EXCEPTION 'slot ledger rows are never deleted' USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
END
$$;
COMMENT ON FUNCTION plaid_link_slot_no_delete() IS
    'Only a household cascade removes slot ledger rows (ADR 0034).';
REVOKE ALL ON FUNCTION plaid_link_slot_no_delete() FROM PUBLIC;
CREATE TRIGGER plaid_link_slot_no_delete
    BEFORE DELETE ON plaid_link_slot
    FOR EACH ROW EXECUTE FUNCTION plaid_link_slot_no_delete();
"""

SLOT_COUNT_SQL = """
CREATE FUNCTION plaid_slot_count(client_fp text, env text) RETURNS integer
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
    SELECT count(*)::integer FROM public.plaid_link_slot s
     WHERE s.client_fp = $1 AND s.env = $2 AND s.state <> 'abandoned'
$$;
COMMENT ON FUNCTION plaid_slot_count(text, text) IS
    'Slots a Plaid client has used in one env, across households: a number only (ADR 0034).';
REVOKE ALL ON FUNCTION plaid_slot_count(text, text) FROM PUBLIC;
"""


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY household_isolation ON "{table}" FOR ALL'
        " USING (household_id = app_household_id())"
        " WITH CHECK (household_id = app_household_id())"
    )


def _restrictive(name: str, table: str, command: str, using: str | None, check: str | None) -> None:
    sql = f'CREATE POLICY {name} ON "{table}" AS RESTRICTIVE FOR {command}'
    if using is not None:
        sql += f" USING ({using})"
    if check is not None:
        sql += f" WITH CHECK ({check})"
    op.execute(sql)


def _swap_check(table: str, name: str, expr: str) -> None:
    op.drop_constraint(op.f(f"ck_{table}_{name}"), table, type_="check")
    op.create_check_constraint(op.f(f"ck_{table}_{name}"), table, expr)


def upgrade() -> None:
    # bank_connection
    op.add_column("bank_connection", sa.Column("client_fp", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_bank_connection_client_fp_format"),
        "bank_connection",
        f"client_fp IS NULL OR client_fp ~ '{CLIENT_FP_PATTERN}'",
    )
    _swap_check("bank_connection", "status", f"status IN ({NEW_STATUSES})")

    # account: the statement key (backfilled) takes over the per-household import key.
    op.add_column("account", sa.Column("statement_key", sa.Text(), nullable=True))
    op.add_column("account", sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=True))
    # FORCE RLS binds the owner, so without a household context this UPDATE would see no rows.
    op.execute("ALTER TABLE account NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE account SET statement_key = external_account_id WHERE source = 'import'")
    op.execute("ALTER TABLE account FORCE ROW LEVEL SECURITY")
    op.drop_index("uq_account_household_id_external_account_id_import", table_name="account")
    op.create_unique_constraint(
        op.f("uq_account_household_id_statement_key"), "account", ["household_id", "statement_key"]
    )
    op.create_check_constraint(
        op.f("ck_account_adopted_is_connected"),
        "account",
        "adopted_at IS NULL OR (statement_key IS NOT NULL AND source IN ('plaid', 'simplefin'))",
    )
    op.execute(ACCOUNT_SOURCE_GUARD_SQL)

    # balance_snapshot: which connection wrote it (NULL: manual or older rows).
    op.add_column("balance_snapshot", sa.Column("bank_connection_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_balance_snapshot_bank_connection_id"), "balance_snapshot", ["bank_connection_id"]
    )
    op.create_foreign_key(
        op.f("fk_balance_snapshot_bank_connection_id_bank_connection"),
        "balance_snapshot",
        "bank_connection",
        ["bank_connection_id", "household_id"],
        ["id", "household_id"],
        ondelete="SET NULL (bank_connection_id)",
    )

    # sync_run: supersession, coverage and held counts, and Plaid's history flag.
    for column in ("rows_superseded", "rows_skipped_covered", "rows_held"):
        op.add_column(
            "sync_run",
            sa.Column(column, sa.Integer(), server_default=sa.text("0"), nullable=False),
        )
    op.add_column(
        "sync_run",
        sa.Column(
            "history_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    _swap_check("sync_run", "counts_non_negative", NEW_SYNC_COUNTS)

    # job_run: who asked (their rights were checked at enqueue).
    op.add_column("job_run", sa.Column("requested_by", sa.Uuid(), nullable=True))
    op.create_index(op.f("ix_job_run_requested_by"), "job_run", ["requested_by"])
    op.create_foreign_key(
        op.f("fk_job_run_requested_by_person"),
        "job_run",
        "person",
        ["requested_by", "household_id"],
        ["id", "household_id"],
        ondelete="SET NULL (requested_by)",
    )

    _create_connector_account_link()
    _create_plaid_link_slot()
    _create_account_coverage()

    _enable_rls("connector_account_link")

    _enable_rls("plaid_link_slot")
    op.execute(f"REVOKE DELETE, TRUNCATE ON plaid_link_slot FROM {APP_ROLE}")
    op.execute(SLOT_GUARD_SQL)
    op.execute(SLOT_NO_DELETE_SQL)
    # FORCE RLS binds the function's owner too: a SELECT-only policy for the owner lets
    # plaid_slot_count() read every household's rows. The app role gets EXECUTE only.
    op.execute(
        f"CREATE POLICY plaid_link_slot_count_owner ON plaid_link_slot FOR SELECT TO {OWNER_ROLE}"
        " USING (true)"
    )
    op.execute(SLOT_COUNT_SQL)
    op.execute(f"GRANT EXECUTE ON FUNCTION plaid_slot_count(text, text) TO {APP_ROLE}")

    _enable_rls("account_coverage")
    _restrictive("person_read", "account_coverage", "SELECT", COVERAGE_READ, None)
    _restrictive("person_insert", "account_coverage", "INSERT", None, COVERAGE_WRITE)
    _restrictive("person_update", "account_coverage", "UPDATE", COVERAGE_WRITE, COVERAGE_WRITE)
    _restrictive("person_delete", "account_coverage", "DELETE", COVERAGE_WRITE, None)


def downgrade() -> None:
    # Dropping a table drops its policies, triggers and grants (DROP TABLE fires no DELETE
    # trigger). Functions go after the tables whose triggers use them.
    op.drop_table("account_coverage")
    op.drop_table("plaid_link_slot")
    op.drop_table("connector_account_link")
    op.execute("DROP FUNCTION plaid_slot_count(text, text)")
    op.execute("DROP FUNCTION plaid_link_slot_no_delete()")
    op.execute("DROP FUNCTION plaid_link_slot_guard()")

    op.drop_constraint(op.f("fk_job_run_requested_by_person"), "job_run", type_="foreignkey")
    op.drop_index(op.f("ix_job_run_requested_by"), table_name="job_run")
    op.drop_column("job_run", "requested_by")

    _swap_check("sync_run", "counts_non_negative", OLD_SYNC_COUNTS)
    for column in ("history_complete", "rows_held", "rows_skipped_covered", "rows_superseded"):
        op.drop_column("sync_run", column)

    op.drop_constraint(
        op.f("fk_balance_snapshot_bank_connection_id_bank_connection"),
        "balance_snapshot",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_balance_snapshot_bank_connection_id"), table_name="balance_snapshot")
    op.drop_column("balance_snapshot", "bank_connection_id")

    op.execute("DROP TRIGGER account_guard_source ON account")
    op.execute("DROP FUNCTION account_guard_source()")
    op.drop_constraint(op.f("ck_account_adopted_is_connected"), "account", type_="check")
    op.drop_constraint(op.f("uq_account_household_id_statement_key"), "account", type_="unique")
    op.create_index(
        "uq_account_household_id_external_account_id_import",
        "account",
        ["household_id", "external_account_id"],
        unique=True,
        postgresql_where="source = 'import'",
    )
    op.drop_column("account", "adopted_at")
    op.drop_column("account", "statement_key")

    # A pending_setup connection ingested nothing; disconnected keeps every job off it too.
    op.execute("ALTER TABLE bank_connection NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE bank_connection SET status = 'disconnected' WHERE status = 'pending_setup'")
    op.execute("ALTER TABLE bank_connection FORCE ROW LEVEL SECURITY")
    _swap_check("bank_connection", "status", f"status IN ({OLD_STATUSES})")
    op.drop_constraint(
        op.f("ck_bank_connection_client_fp_format"), "bank_connection", type_="check"
    )
    op.drop_column("bank_connection", "client_fp")


def _create_connector_account_link() -> None:
    op.create_table(
        "connector_account_link",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("bank_connection_id", sa.Uuid(), nullable=False),
        sa.Column("external_account_id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'mapped', 'ignored')",
            name=op.f("ck_connector_account_link_state"),
        ),
        sa.CheckConstraint(
            _code_check("status_code"), name=op.f("ck_connector_account_link_status_code_is_code")
        ),
        sa.CheckConstraint(
            "(state = 'mapped') = (account_id IS NOT NULL)",
            name=op.f("ck_connector_account_link_mapped_has_account"),
        ),
        sa.CheckConstraint(
            "(state = 'pending') = (decided_at IS NULL)",
            name=op.f("ck_connector_account_link_decided_when_done"),
        ),
        sa.ForeignKeyConstraint(
            ["bank_connection_id", "household_id"],
            ["bank_connection.id", "bank_connection.household_id"],
            name=op.f("fk_connector_account_link_bank_connection_id_bank_connection"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_connector_account_link_account_id_account"),
            ondelete="NO ACTION",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_connector_account_link_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_account_link")),
        sa.UniqueConstraint(
            "bank_connection_id",
            "external_account_id",
            name=op.f("uq_connector_account_link_bank_connection_id_external_account_id"),
        ),
    )
    op.create_index(
        op.f("ix_connector_account_link_account_id"), "connector_account_link", ["account_id"]
    )
    op.create_index(
        op.f("ix_connector_account_link_household_id"), "connector_account_link", ["household_id"]
    )


def _create_plaid_link_slot() -> None:
    op.create_table(
        "plaid_link_slot",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("client_fp", sa.Text(), nullable=False),
        sa.Column("env", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), server_default=sa.text("'reserved'"), nullable=False),
        sa.Column("link_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("owner_count", sa.SmallInteger(), nullable=True),
        sa.Column("item_id", sa.Text(), nullable=True),
        sa.Column("bank_connection_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flag", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "state IN ('reserved', 'linked', 'abandoned', 'unknown')",
            name=op.f("ck_plaid_link_slot_state"),
        ),
        sa.CheckConstraint("env IN ('sandbox', 'production')", name=op.f("ck_plaid_link_slot_env")),
        sa.CheckConstraint(_code_check("flag"), name=op.f("ck_plaid_link_slot_flag_is_code")),
        sa.CheckConstraint(
            f"client_fp ~ '{CLIENT_FP_PATTERN}'", name=op.f("ck_plaid_link_slot_client_fp_format")
        ),
        sa.CheckConstraint(
            "state = 'reserved' OR link_token_enc IS NULL",
            name=op.f("ck_plaid_link_slot_link_token_only_while_reserved"),
        ),
        sa.CheckConstraint(
            "owner_count IS NULL OR owner_count BETWEEN 0 AND 10",
            name=op.f("ck_plaid_link_slot_owner_count_range"),
        ),
        sa.CheckConstraint(
            "env <> 'production' OR owner_count IS NOT NULL",
            name=op.f("ck_plaid_link_slot_production_has_owner_count"),
        ),
        sa.CheckConstraint(
            "state <> 'linked' OR item_id IS NOT NULL",
            name=op.f("ck_plaid_link_slot_linked_has_item"),
        ),
        sa.CheckConstraint(
            "(state = 'reserved') = (resolved_at IS NULL)",
            name=op.f("ck_plaid_link_slot_resolved_when_done"),
        ),
        sa.ForeignKeyConstraint(
            ["bank_connection_id", "household_id"],
            ["bank_connection.id", "bank_connection.household_id"],
            name=op.f("fk_plaid_link_slot_bank_connection_id_bank_connection"),
            ondelete="SET NULL (bank_connection_id)",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_plaid_link_slot_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plaid_link_slot")),
        sa.UniqueConstraint("item_id", name=op.f("uq_plaid_link_slot_item_id")),
    )
    op.create_index(
        op.f("ix_plaid_link_slot_bank_connection_id"), "plaid_link_slot", ["bank_connection_id"]
    )
    op.create_index(op.f("ix_plaid_link_slot_household_id"), "plaid_link_slot", ["household_id"])
    op.create_index("ix_plaid_link_slot_client_fp_env", "plaid_link_slot", ["client_fp", "env"])
    op.create_index(
        "uq_plaid_link_slot_client_fp_env_reserved",
        "plaid_link_slot",
        ["client_fp", "env"],
        unique=True,
        postgresql_where="state = 'reserved'",
    )


def _create_account_coverage() -> None:
    op.create_table(
        "account_coverage",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("source_import_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("start_date <= end_date", name=op.f("ck_account_coverage_period_order")),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_account_coverage_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_import_run_id", "household_id"],
            ["import_run.id", "import_run.household_id"],
            name=op.f("fk_account_coverage_source_import_run_id_import_run"),
            ondelete="SET NULL (source_import_run_id)",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_account_coverage_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_coverage")),
    )
    op.create_index(
        "ix_account_coverage_account_id_start_date",
        "account_coverage",
        ["account_id", "start_date"],
    )
    op.create_index(op.f("ix_account_coverage_household_id"), "account_coverage", ["household_id"])
    op.create_index(
        op.f("ix_account_coverage_source_import_run_id"),
        "account_coverage",
        ["source_import_run_id"],
    )
