"""Import runs (statements and exports) and connector sync runs (ADR 0017).

``import_run`` is one attempt to turn a ``document`` into transactions and records the statement
reconciliation (PLAN 8.1 step 4). ``sync_run`` is one connector sync of a ``bank_connection``
(Plaid / SimpleFIN) with its cursor bookkeeping. Both are executed as F2 jobs (ADR 0016): the job
payload carries only ``household_id`` and the run's id.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Computed, Index, Numeric, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    IMPORT_RUN_STATUSES,
    PROFILE_ID_MAX,
    PROFILE_ID_PATTERN,
    RECONCILE_STATUSES,
    RECONCILE_TOLERANCE_SQL,
    SYNC_RUN_STATUSES,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    check_error_class,
    check_in,
    child_fk,
    parent_key,
)

# closing - (opening + sum). NULL until both balances and the sum are known.
BALANCE_DIFF_SQL = "closing_balance - opening_balance - txn_sum"


class ImportRun(Base):
    """One import of one document, plus its reconciliation result.

    **Reconciliation** (hard rule 6). Balances use Holdfast's sign convention (assets positive,
    owed negative, as ``balance_snapshot.current``), so ``opening + Σ txns = closing`` holds for
    cards too. ``balance_diff`` is a generated column, so it can't disagree with the numbers.
    ``reconcile_status``:

    - ``pending`` → ``reconciled`` (CHECK: all three numbers present, ``|diff| <= 0.01``) or
      ``needs_review`` (anything else: a diff, missing balances, a parse doubt).
    - ``needs_review`` → ``accepted`` only, and only with ``reviewed_by_user_id`` and
      ``reviewed_at`` (CHECK). ``accepted`` is never written on insert.
    - Once out of ``pending`` the result is final: balances, sum, period, account and document
      can't change (trigger ``import_run_guard``). Re-reconciling means a new run.
    - A succeeded run has left ``pending``: ``reconcile_status`` is ``reconciled``,
      ``needs_review``, or ``accepted``.

    - Rows join a run only while it is ``running`` and ``pending`` (see ``Transaction``). Once
      a run has rows, its account and document can't change. A run that moves to ``failed``
      with rows still ``pending`` becomes ``needs_review``, so the document latches.

    Flagging a run sets the document's ``parse_status`` to ``needs_review`` and latches that
    flag. The latch clears only when a run is accepted and no sibling still needs review.
    Deleting the run leaves the latch set.

    **Visibility.** A run is visible only with its document. A run with an account follows that
    account like ``balance_snapshot`` (its balances are summary data): hidden from others when
    the account is private, writable only by the owner, for joint accounts, or by household-wide
    jobs. A run with no account (a multi-account export) holds no balances and no transaction
    sum (CHECK). Writing any run also needs its document to be the writer's or the household's
    (``person_id`` NULL), unless it's a household-wide job: one person can't flag, accept or
    add rows to a run on another person's statement.

    **Counts.** ``rows_parsed`` counts every row read from the file. Of those, ``rows_inserted``
    became new transactions and ``rows_duplicate`` matched an existing fingerprint (re-import).
    ``rows_superseded`` of the inserted rows were superseded on arrival by an existing Plaid row.

    ``reviewed_by_user_id`` has no FK, like ``audit_log.user_id``: deleting a user must not
    rewrite review history. It is advisory: the database can't tell who the session's user is,
    so the audit log is the record of who accepted. The accept service must take the id from
    the authenticated session, never from the request.
    """

    __tablename__ = "import_run"
    __table_args__ = (
        check_in("status", IMPORT_RUN_STATUSES),
        check_in("reconcile_status", RECONCILE_STATUSES),
        check_error_class(),
        child_fk("document_id", "document"),
        child_fk("account_id", "account"),
        CheckConstraint(
            f"profile IS NULL OR (length(profile) <= {PROFILE_ID_MAX}"
            f" AND profile ~ '{PROFILE_ID_PATTERN}')",
            name="profile_format",
        ),
        CheckConstraint(
            "period_start IS NULL OR period_end IS NULL OR period_start <= period_end",
            name="period_order",
        ),
        CheckConstraint(
            "rows_parsed >= 0 AND rows_inserted >= 0 AND rows_duplicate >= 0"
            " AND rows_superseded >= 0"
            " AND rows_inserted + rows_duplicate <= rows_parsed"
            " AND rows_superseded <= rows_inserted",
            name="counts_consistent",
        ),
        CheckConstraint(
            "account_id IS NOT NULL OR (opening_balance IS NULL AND closing_balance IS NULL"
            " AND txn_sum IS NULL)",
            name="balances_need_account",
        ),
        CheckConstraint(
            "reconcile_status <> 'reconciled' OR (opening_balance IS NOT NULL"
            " AND closing_balance IS NOT NULL AND txn_sum IS NOT NULL"
            f" AND abs(balance_diff) <= {RECONCILE_TOLERANCE_SQL})",
            name="reconciled_within_tolerance",
        ),
        CheckConstraint(
            "(reconcile_status = 'accepted') = (reviewed_at IS NOT NULL)"
            " AND (reviewed_at IS NULL) = (reviewed_by_user_id IS NULL)",
            name="accepted_is_attributed",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL)",
            name="finished_when_done",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR reconcile_status IN"
            " ('reconciled', 'needs_review', 'accepted')",
            name="succeeded_is_reconciled",
        ),
        CheckConstraint("error_class IS NULL OR status = 'failed'", name="error_only_on_failure"),
        CheckConstraint(
            "review_reason IS NULL OR (length(review_reason) <= 120"
            " AND review_reason ~ '^[a-z][a-z0-9_]*(,[a-z][a-z0-9_]*)*$')",
            name="review_reason_format",
        ),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    document_id: Mapped[uuid.UUID] = mapped_column(index=True)
    # The statement's account. NULL until detected / chosen, or for multi-account exports.
    account_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    # Profile used for this run (may differ from document.detected_profile if the user chose).
    profile: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default=text("'queued'"))
    reconcile_status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    period_start: Mapped[date | None]
    period_end: Mapped[date | None]
    opening_balance: Mapped[Money | None] = mapped_column(
        comment="Statement opening balance, Holdfast sign (assets +, owed -)."
    )
    closing_balance: Mapped[Money | None] = mapped_column(
        comment="Statement closing balance, Holdfast sign (assets +, owed -)."
    )
    txn_sum: Mapped[Money | None] = mapped_column(
        comment="Sum of every parsed transaction amount in the statement (outflow -, inflow +)."
    )
    balance_diff: Mapped[Money | None] = mapped_column(
        Numeric(14, 2),
        Computed(BALANCE_DIFF_SQL, persisted=True),
        comment="closing - (opening + txn_sum). Reconciled iff |diff| <= 0.01.",
    )
    rows_parsed: Mapped[int] = mapped_column(server_default=text("0"))
    rows_inserted: Mapped[int] = mapped_column(server_default=text("0"))
    rows_duplicate: Mapped[int] = mapped_column(server_default=text("0"))
    rows_superseded: Mapped[int] = mapped_column(server_default=text("0"))
    # Exception class name only (CHECK); messages can carry account numbers (hard rule 3).
    error_class: Mapped[str | None]
    # Why the run was flagged: stable code(s) from the parser, comma-joined. Set with the flag.
    # NULL on a reconciled run and on a needs_review run created before the column existed.
    review_reason: Mapped[str | None]
    reviewed_by_user_id: Mapped[uuid.UUID | None]
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class SyncRun(Base):
    """One connector sync of one bank connection (Plaid ``/transactions/sync``, SimpleFIN).

    **Cursor rule** (Phase 2 money review): ``bank_connection.tx_cursor`` is the committed
    cursor and is written in the same database transaction as the batch it covers. A run records
    ``cursor_before`` (where it started) and ``cursor_after`` (what it committed); the CHECK
    ``cursor_after_only_on_success`` means a failed or running sync never claims a cursor.

    Household-visible, like ``bank_connection``: it holds counts and cursors, no amounts.
    Plaid cursors are opaque paging tokens, not credentials.
    """

    __tablename__ = "sync_run"
    __table_args__ = (
        check_in("status", SYNC_RUN_STATUSES),
        check_error_class(),
        child_fk("bank_connection_id", "bank_connection"),
        CheckConstraint(
            "rows_added >= 0 AND rows_modified >= 0 AND rows_removed >= 0 AND pages >= 0"
            " AND rows_superseded >= 0 AND rows_skipped_covered >= 0 AND rows_held >= 0",
            name="counts_non_negative",
        ),
        CheckConstraint(
            "cursor_after IS NULL OR status = 'succeeded'", name="cursor_after_only_on_success"
        ),
        CheckConstraint("(status = 'running') = (finished_at IS NULL)", name="finished_when_done"),
        CheckConstraint("error_class IS NULL OR status = 'failed'", name="error_only_on_failure"),
        # Sync Health: latest runs per connection. Also serves the FK.
        Index("ix_sync_run_bank_connection_id_started_at", "bank_connection_id", "started_at"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    bank_connection_id: Mapped[uuid.UUID]
    status: Mapped[str] = mapped_column(server_default=text("'running'"))
    cursor_before: Mapped[str | None]
    cursor_after: Mapped[str | None]
    pages: Mapped[int] = mapped_column(server_default=text("0"))
    rows_added: Mapped[int] = mapped_column(server_default=text("0"))
    rows_modified: Mapped[int] = mapped_column(server_default=text("0"))
    rows_removed: Mapped[int] = mapped_column(server_default=text("0"))
    # ADR 0035: import rows this sync superseded, posted rows skipped inside coverage, and
    # matched pairs held for the owner instead of superseded.
    rows_superseded: Mapped[int] = mapped_column(server_default=text("0"))
    rows_skipped_covered: Mapped[int] = mapped_column(server_default=text("0"))
    rows_held: Mapped[int] = mapped_column(server_default=text("0"))
    # The connector reported its full history as loaded (Plaid HISTORICAL_UPDATE_COMPLETE).
    history_complete: Mapped[bool] = mapped_column(server_default=text("false"))
    error_class: Mapped[str | None]
    started_at: Mapped[CreatedAt]
    finished_at: Mapped[datetime | None]
