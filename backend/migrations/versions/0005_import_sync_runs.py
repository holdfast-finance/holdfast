"""Import and sync runs, statement reconciliation, transaction supersession (ADR 0017).

Tables come from autogenerate (reviewed by hand). Written out below because autogenerate can't
see them:

- ``import_run``: ENABLE + FORCE RLS, ``household_isolation``, and RESTRICTIVE per-person
  policies like ``balance_snapshot`` (a run's balances follow its account's visibility; a run
  with no account holds no balances and no transaction sum, and is household-visible).
- ``sync_run``: ENABLE + FORCE RLS, ``household_isolation`` (household-visible, like
  ``bank_connection``).
- CHECKs on the existing ``document`` and ``transaction`` tables (autogenerate misses CHECKs on
  existing tables): document size, relative storage path, profile id; imported rows carry a hex
  fingerprint, provenance only on imports, no self-supersession.
- ``import_run_guard``: reconciliation is final once out of ``pending``; ``needs_review`` leaves
  only to ``accepted``; ``accepted`` is never inserted; a review can't be rewritten. The status
  error is a fixed string.
- ``import_run_flag_document`` / ``import_run_accept_document`` / ``document_review_guard``: a
  flagged run latches its document. The latch clears only when a run is accepted and no sibling
  run still needs review. Deleting the run does not clear it.
- ``transaction_import_run_guard``: ``import_run_id``, once set, cannot be changed or cleared.
- ``transaction_supersession_guard``: no supersession chains. The winner and any row this one
  already replaces are locked before the check.

The functions that read or write another table switch ``app.household_wide`` on for their
own query and restore the caller's value, like ``transaction_split_balance`` (0002), so a
person-scoped policy can never make them see less and fail open.

Grants come from the owner's default privileges (SELECT/INSERT/UPDATE/DELETE for holdfast_app).
``document.size_bytes`` is NOT NULL with no default: no code writes documents before Phase 3, so
the table is empty; on a database where it isn't, the upgrade fails loudly rather than invent sizes.

Patterns are copied from ``app.models._common`` (migrations never import app code).

Revision ID: 0005_import_sync_runs
Revises: 0004_job_run
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_import_sync_runs"
down_revision: str | None = "0004_job_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ERROR_CLASS_SQL = (
    "error_class IS NULL OR (length(error_class) <= 200"
    " AND error_class ~ '^[A-Za-z_][A-Za-z0-9_.]*$')"
)
PROFILE_ID_PATTERN = "^[a-z0-9][a-z0-9_.-]*$"
STORAGE_PATH_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]*(/[A-Za-z0-9][A-Za-z0-9._-]*)*$"
SHA256_HEX_PATTERN = "^[0-9a-f]{64}$"

# import_run: read follows the account (summary_only viewers see balances, like
# balance_snapshot); write needs a writable account (owner, joint) or a household-wide job.
IMPORT_RUN_READ = (
    "account_id IS NULL OR EXISTS (SELECT 1 FROM account a WHERE a.id = import_run.account_id)"
)
IMPORT_RUN_WRITE = (
    "account_id IS NULL OR app_household_wide() OR EXISTS (SELECT 1 FROM account a"
    " WHERE a.id = import_run.account_id"
    " AND (a.owner_person_id IS NULL OR a.owner_person_id = app_person_id()))"
)

# Messages carry ids and status names only, never amounts.
IMPORT_RUN_GUARD_SQL = """
CREATE FUNCTION import_run_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.reconcile_status = 'accepted' THEN
            RAISE EXCEPTION 'import run % can only be accepted after review', NEW.id
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.reconcile_status <> 'pending' AND (
           NEW.opening_balance IS DISTINCT FROM OLD.opening_balance
        OR NEW.closing_balance IS DISTINCT FROM OLD.closing_balance
        OR NEW.txn_sum IS DISTINCT FROM OLD.txn_sum
        OR NEW.period_start IS DISTINCT FROM OLD.period_start
        OR NEW.period_end IS DISTINCT FROM OLD.period_end
        OR NEW.account_id IS DISTINCT FROM OLD.account_id
        OR NEW.document_id IS DISTINCT FROM OLD.document_id)
    THEN
        RAISE EXCEPTION 'reconciliation of import run % is final; start a new run', OLD.id
            USING ERRCODE = '23514';
    END IF;
    IF NEW.reconcile_status IS DISTINCT FROM OLD.reconcile_status AND NOT (
           (OLD.reconcile_status = 'pending'
            AND NEW.reconcile_status IN ('reconciled', 'needs_review'))
        OR (OLD.reconcile_status = 'needs_review' AND NEW.reconcile_status = 'accepted'))
    THEN
        RAISE EXCEPTION 'import run %: reconcile status cannot change', OLD.id
            USING ERRCODE = '23514';
    END IF;
    IF OLD.reviewed_at IS NOT NULL AND (
           NEW.reviewed_at IS DISTINCT FROM OLD.reviewed_at
        OR NEW.reviewed_by_user_id IS DISTINCT FROM OLD.reviewed_by_user_id)
    THEN
        RAISE EXCEPTION 'the review of import run % is final', OLD.id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION import_run_guard() IS
    'Reconciliation is final once decided; needs_review leaves only via an attributed accept.';
CREATE TRIGGER import_run_guard
    BEFORE INSERT OR UPDATE ON import_run
    FOR EACH ROW EXECUTE FUNCTION import_run_guard();

CREATE FUNCTION import_run_flag_document() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    prev_wide text := current_setting('app.household_wide', true);
BEGIN
    PERFORM set_config('app.household_wide', 'on', true);
    UPDATE public.document
       SET parse_status = 'needs_review',
           import_review_latched = true
     WHERE id = NEW.document_id
       AND (parse_status <> 'needs_review' OR NOT import_review_latched);
    PERFORM set_config('app.household_wide', coalesce(prev_wide, ''), true);
    RETURN NULL;
END
$$;
COMMENT ON FUNCTION import_run_flag_document() IS
    'A needs_review run flags its document and latches that flag (PLAN 8.1 step 4).';
CREATE TRIGGER import_run_flag_document
    AFTER INSERT OR UPDATE OF reconcile_status ON import_run
    FOR EACH ROW WHEN (NEW.reconcile_status = 'needs_review')
    EXECUTE FUNCTION import_run_flag_document();

CREATE FUNCTION import_run_accept_document() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    prev_wide text := current_setting('app.household_wide', true);
    still_open boolean;
BEGIN
    PERFORM set_config('app.household_wide', 'on', true);
    SELECT EXISTS (
        SELECT 1 FROM public.import_run r
         WHERE r.document_id = NEW.document_id
           AND r.id <> NEW.id
           AND r.reconcile_status = 'needs_review'
    ) INTO still_open;
    IF NOT still_open THEN
        UPDATE public.document
           SET import_review_latched = false
         WHERE id = NEW.document_id AND import_review_latched;
    END IF;
    PERFORM set_config('app.household_wide', coalesce(prev_wide, ''), true);
    RETURN NULL;
END
$$;
COMMENT ON FUNCTION import_run_accept_document() IS
    'Accepting the last open run clears the document latch. Nothing else does.';
CREATE TRIGGER import_run_accept_document
    AFTER UPDATE OF reconcile_status ON import_run
    FOR EACH ROW
    WHEN (NEW.reconcile_status = 'accepted' AND OLD.reconcile_status IS DISTINCT FROM 'accepted')
    EXECUTE FUNCTION import_run_accept_document();

CREATE FUNCTION document_review_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    prev_wide text := current_setting('app.household_wide', true);
    accepted boolean;
    still_open boolean;
BEGIN
    IF OLD.import_review_latched AND NOT NEW.import_review_latched THEN
        PERFORM set_config('app.household_wide', 'on', true);
        SELECT EXISTS (
            SELECT 1 FROM public.import_run r
             WHERE r.document_id = NEW.id AND r.reconcile_status = 'accepted'
        ) INTO accepted;
        SELECT EXISTS (
            SELECT 1 FROM public.import_run r
             WHERE r.document_id = NEW.id AND r.reconcile_status = 'needs_review'
        ) INTO still_open;
        PERFORM set_config('app.household_wide', coalesce(prev_wide, ''), true);
        IF still_open OR NOT accepted THEN
            RAISE EXCEPTION
                'document % review clears only when its import run is accepted', NEW.id
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF OLD.parse_status = 'needs_review' AND NEW.parse_status <> 'needs_review'
       AND NEW.import_review_latched THEN
        RAISE EXCEPTION 'document % has an import run that needs review; accept the run first',
            NEW.id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION document_review_guard() IS
    'A latched document stays needs_review until a run is accepted (hard rule 6).';
CREATE TRIGGER document_review_guard
    BEFORE UPDATE OF parse_status, import_review_latched ON document
    FOR EACH ROW
    WHEN (
        (OLD.parse_status = 'needs_review' AND NEW.parse_status <> 'needs_review')
        OR (OLD.import_review_latched AND NOT NEW.import_review_latched)
    )
    EXECUTE FUNCTION document_review_guard();
"""

# Once set, the link to the run that created a row is final. Clearing it would let that
# run be deleted while its rows stayed. The document latch still blocks a parsed status.
IMPORT_RUN_LINK_SQL = """
CREATE FUNCTION transaction_import_run_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    IF OLD.import_run_id IS NOT NULL
       AND NEW.import_run_id IS DISTINCT FROM OLD.import_run_id THEN
        RAISE EXCEPTION 'transaction % import run link is final', OLD.id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION transaction_import_run_guard() IS
    'import_run_id, once set, cannot be changed or cleared (ADR 0017).';
CREATE TRIGGER transaction_import_run_guard
    BEFORE UPDATE OF import_run_id ON "transaction"
    FOR EACH ROW EXECUTE FUNCTION transaction_import_run_guard();
"""

# The FK already keeps supersession inside one account and the partial unique index makes it
# one-to-one; this forbids chains: the winner must be live, and a row that replaces another
# can't itself be replaced. The rows are locked first so two sessions cannot each miss the
# other's link. The job lock (ADR 0016) is extra, not the only serialization.
SUPERSESSION_GUARD_SQL = """
CREATE FUNCTION transaction_supersession_guard() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    prev_wide text := current_setting('app.household_wide', true);
    winner_superseded boolean;
    replaces_another boolean;
BEGIN
    IF NEW.superseded_by_id IS NULL THEN
        RETURN NEW;
    END IF;
    PERFORM set_config('app.household_wide', 'on', true);
    PERFORM id FROM public."transaction"
     WHERE id = NEW.superseded_by_id OR superseded_by_id = NEW.id
     ORDER BY id
     FOR UPDATE;
    SELECT EXISTS (
        SELECT 1 FROM public."transaction" w
         WHERE w.id = NEW.superseded_by_id AND w.superseded_by_id IS NOT NULL
    ) INTO winner_superseded;
    SELECT EXISTS (
        SELECT 1 FROM public."transaction" l WHERE l.superseded_by_id = NEW.id
    ) INTO replaces_another;
    PERFORM set_config('app.household_wide', coalesce(prev_wide, ''), true);
    IF winner_superseded THEN
        RAISE EXCEPTION 'transaction % is superseded and cannot replace another row',
            NEW.superseded_by_id
            USING ERRCODE = '23514';
    END IF;
    IF replaces_another THEN
        RAISE EXCEPTION 'transaction % replaces another row and cannot itself be superseded',
            NEW.id
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;
COMMENT ON FUNCTION transaction_supersession_guard() IS
    'No supersession chains: a winner is live and a winner is never superseded (ADR 0017).';
CREATE TRIGGER transaction_supersession_guard
    BEFORE INSERT OR UPDATE OF superseded_by_id ON "transaction"
    FOR EACH ROW EXECUTE FUNCTION transaction_supersession_guard();
"""


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY household_isolation ON "{table}" FOR ALL'
        " USING (household_id = app_household_id())"
        " WITH CHECK (household_id = app_household_id())"
    )


def _restrictive(name: str, command: str, using: str | None, check: str | None) -> None:
    sql = f"CREATE POLICY {name} ON import_run AS RESTRICTIVE FOR {command}"
    if using is not None:
        sql += f" USING ({using})"
    if check is not None:
        sql += f" WITH CHECK ({check})"
    op.execute(sql)


def upgrade() -> None:
    _create_sync_run()
    _create_import_run()

    # document: size, detected profile, a storage path inside DOCUMENT_STORE, and a review
    # latch that outlives the import run that raised it.
    op.add_column("document", sa.Column("size_bytes", sa.BigInteger(), nullable=False))
    op.add_column(
        "document",
        sa.Column(
            "import_review_latched",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column("document", sa.Column("detected_profile", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_document_size_bytes_positive"), "document", "size_bytes > 0"
    )
    op.create_check_constraint(
        op.f("ck_document_storage_path_relative"),
        "document",
        f"length(storage_path) <= 512 AND storage_path ~ '{STORAGE_PATH_PATTERN}'",
    )
    op.create_check_constraint(
        op.f("ck_document_detected_profile_format"),
        "document",
        "detected_profile IS NULL OR (length(detected_profile) <= 100"
        f" AND detected_profile ~ '{PROFILE_ID_PATTERN}')",
    )

    # transaction: import provenance and supersession.
    op.add_column("transaction", sa.Column("import_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "transaction",
        sa.Column(
            "superseded_by_id",
            sa.Uuid(),
            nullable=True,
            comment="Row that replaces this one (Plaid wins). Superseded rows are excluded.",
        ),
    )
    op.create_index(
        op.f("ix_transaction_import_run_id"), "transaction", ["import_run_id"], unique=False
    )
    op.create_unique_constraint(
        op.f("uq_transaction_id_account_id"), "transaction", ["id", "account_id"]
    )
    op.create_index(
        "uq_transaction_superseded_by_id",
        "transaction",
        ["superseded_by_id"],
        unique=True,
        postgresql_where=sa.text("superseded_by_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("fk_transaction_superseded_by_id_transaction"),
        "transaction",
        "transaction",
        ["superseded_by_id", "account_id"],
        ["id", "account_id"],
        ondelete="SET NULL (superseded_by_id)",
    )
    op.create_foreign_key(
        op.f("fk_transaction_import_run_id_import_run"),
        "transaction",
        "import_run",
        ["import_run_id", "household_id"],
        ["id", "household_id"],
        ondelete="NO ACTION",
    )
    op.create_check_constraint(
        op.f("ck_transaction_not_self_superseded"),
        "transaction",
        "superseded_by_id IS NULL OR superseded_by_id <> id",
    )
    op.create_check_constraint(
        op.f("ck_transaction_import_run_source"),
        "transaction",
        "import_run_id IS NULL OR source = 'import'",
    )
    op.create_check_constraint(
        op.f("ck_transaction_import_fingerprinted"),
        "transaction",
        "source <> 'import' OR import_fingerprint IS NOT NULL",
    )
    op.create_check_constraint(
        op.f("ck_transaction_import_fingerprint_hex"),
        "transaction",
        f"import_fingerprint IS NULL OR import_fingerprint ~ '{SHA256_HEX_PATTERN}'",
    )

    # RLS.
    _enable_rls("sync_run")
    _enable_rls("import_run")
    _restrictive("person_read", "SELECT", IMPORT_RUN_READ, None)
    _restrictive("person_insert", "INSERT", None, IMPORT_RUN_WRITE)
    _restrictive("person_update", "UPDATE", IMPORT_RUN_WRITE, IMPORT_RUN_WRITE)
    _restrictive("person_delete", "DELETE", IMPORT_RUN_WRITE, None)

    op.execute(IMPORT_RUN_GUARD_SQL)
    op.execute(IMPORT_RUN_LINK_SQL)
    op.execute(SUPERSESSION_GUARD_SQL)


def downgrade() -> None:
    # Triggers on surviving tables first; dropping import_run drops its own triggers and policies.
    op.execute('DROP TRIGGER transaction_supersession_guard ON "transaction"')
    op.execute('DROP TRIGGER transaction_import_run_guard ON "transaction"')
    op.execute("DROP TRIGGER document_review_guard ON document")

    for name in (
        "import_fingerprint_hex",
        "import_fingerprinted",
        "import_run_source",
        "not_self_superseded",
    ):
        op.drop_constraint(op.f(f"ck_transaction_{name}"), "transaction", type_="check")
    op.drop_constraint(
        op.f("fk_transaction_import_run_id_import_run"), "transaction", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_transaction_superseded_by_id_transaction"), "transaction", type_="foreignkey"
    )
    op.drop_index("uq_transaction_superseded_by_id", table_name="transaction")
    op.drop_constraint(op.f("uq_transaction_id_account_id"), "transaction", type_="unique")
    op.drop_index(op.f("ix_transaction_import_run_id"), table_name="transaction")
    op.drop_column("transaction", "superseded_by_id")
    op.drop_column("transaction", "import_run_id")

    for name in ("detected_profile_format", "storage_path_relative", "size_bytes_positive"):
        op.drop_constraint(op.f(f"ck_document_{name}"), "document", type_="check")
    op.drop_column("document", "detected_profile")
    op.drop_column("document", "import_review_latched")
    op.drop_column("document", "size_bytes")

    op.drop_table("import_run")
    op.drop_table("sync_run")

    op.execute("DROP FUNCTION transaction_supersession_guard()")
    op.execute("DROP FUNCTION transaction_import_run_guard()")
    op.execute("DROP FUNCTION document_review_guard()")
    op.execute("DROP FUNCTION import_run_accept_document()")
    op.execute("DROP FUNCTION import_run_flag_document()")
    op.execute("DROP FUNCTION import_run_guard()")


def _create_sync_run() -> None:
    op.create_table(
        "sync_run",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("bank_connection_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column("cursor_before", sa.Text(), nullable=True),
        sa.Column("cursor_after", sa.Text(), nullable=True),
        sa.Column("pages", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_added", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_modified", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_removed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_sync_run_finished_when_done"),
        ),
        sa.CheckConstraint(
            "cursor_after IS NULL OR status = 'succeeded'",
            name=op.f("ck_sync_run_cursor_after_only_on_success"),
        ),
        sa.CheckConstraint(ERROR_CLASS_SQL, name=op.f("ck_sync_run_error_class_is_class_name")),
        sa.CheckConstraint(
            "error_class IS NULL OR status = 'failed'",
            name=op.f("ck_sync_run_error_only_on_failure"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')", name=op.f("ck_sync_run_status")
        ),
        sa.CheckConstraint(
            "rows_added >= 0 AND rows_modified >= 0 AND rows_removed >= 0 AND pages >= 0",
            name=op.f("ck_sync_run_counts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["bank_connection_id", "household_id"],
            ["bank_connection.id", "bank_connection.household_id"],
            name=op.f("fk_sync_run_bank_connection_id_bank_connection"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_sync_run_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_run")),
    )
    op.create_index(
        "ix_sync_run_bank_connection_id_started_at",
        "sync_run",
        ["bank_connection_id", "started_at"],
        unique=False,
    )
    op.create_index(op.f("ix_sync_run_household_id"), "sync_run", ["household_id"], unique=False)


def _create_import_run() -> None:
    op.create_table(
        "import_run",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=True),
        sa.Column("profile", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column(
            "reconcile_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column(
            "opening_balance",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Statement opening balance, Holdfast sign (assets +, owed -).",
        ),
        sa.Column(
            "closing_balance",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="Statement closing balance, Holdfast sign (assets +, owed -).",
        ),
        sa.Column(
            "txn_sum",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment=(
                "Sum of every parsed transaction amount in the statement (outflow -, inflow +)."
            ),
        ),
        sa.Column(
            "balance_diff",
            sa.Numeric(precision=14, scale=2),
            sa.Computed("closing_balance - opening_balance - txn_sum", persisted=True),
            nullable=True,
            comment="closing - (opening + txn_sum). Reconciled iff |diff| <= 0.01.",
        ),
        sa.Column("rows_parsed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_duplicate", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_superseded", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(reconcile_status = 'accepted') = (reviewed_at IS NOT NULL)"
            " AND (reviewed_at IS NULL) = (reviewed_by_user_id IS NULL)",
            name=op.f("ck_import_run_accepted_is_attributed"),
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name=op.f("ck_import_run_finished_when_done"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR reconcile_status IN"
            " ('reconciled', 'needs_review', 'accepted')",
            name=op.f("ck_import_run_succeeded_is_reconciled"),
        ),
        sa.CheckConstraint(ERROR_CLASS_SQL, name=op.f("ck_import_run_error_class_is_class_name")),
        sa.CheckConstraint(
            "error_class IS NULL OR status = 'failed'",
            name=op.f("ck_import_run_error_only_on_failure"),
        ),
        sa.CheckConstraint(
            f"profile IS NULL OR (length(profile) <= 100 AND profile ~ '{PROFILE_ID_PATTERN}')",
            name=op.f("ck_import_run_profile_format"),
        ),
        sa.CheckConstraint(
            "reconcile_status <> 'reconciled' OR (opening_balance IS NOT NULL"
            " AND closing_balance IS NOT NULL AND txn_sum IS NOT NULL"
            " AND abs(balance_diff) <= 0.01)",
            name=op.f("ck_import_run_reconciled_within_tolerance"),
        ),
        sa.CheckConstraint(
            "reconcile_status IN ('pending', 'reconciled', 'needs_review', 'accepted')",
            name=op.f("ck_import_run_reconcile_status"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name=op.f("ck_import_run_status"),
        ),
        sa.CheckConstraint(
            "account_id IS NOT NULL OR (opening_balance IS NULL AND closing_balance IS NULL"
            " AND txn_sum IS NULL)",
            name=op.f("ck_import_run_balances_need_account"),
        ),
        sa.CheckConstraint(
            "period_start IS NULL OR period_end IS NULL OR period_start <= period_end",
            name=op.f("ck_import_run_period_order"),
        ),
        sa.CheckConstraint(
            "rows_parsed >= 0 AND rows_inserted >= 0 AND rows_duplicate >= 0"
            " AND rows_superseded >= 0"
            " AND rows_inserted + rows_duplicate <= rows_parsed"
            " AND rows_superseded <= rows_inserted",
            name=op.f("ck_import_run_counts_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "household_id"],
            ["account.id", "account.household_id"],
            name=op.f("fk_import_run_account_id_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "household_id"],
            ["document.id", "document.household_id"],
            name=op.f("fk_import_run_document_id_document"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_import_run_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_run")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_import_run_id_household_id")),
    )
    op.create_index(op.f("ix_import_run_account_id"), "import_run", ["account_id"], unique=False)
    op.create_index(op.f("ix_import_run_document_id"), "import_run", ["document_id"], unique=False)
    op.create_index(
        op.f("ix_import_run_household_id"), "import_run", ["household_id"], unique=False
    )
