"""Import runs, sync runs, and transaction supersession (ADR 0017).

Reconciliation is enforced in the database: a run is reconciled only when
opening + the sum of its transactions equals closing within one cent. Synthetic
amounts only. A failing statement sits in its own transaction so Postgres can
abort that statement without taking the rest of the test with it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models._common import is_valid_storage_path
from tests.models.conftest import count, delete_household, seed_household, set_ctx, table

pytestmark = pytest.mark.services

RLS_VIOLATION = "row-level security"
WHEN = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)


def _fp(n: int) -> str:
    return f"{n:064x}"


def test_storage_path_stays_inside_the_document_store() -> None:
    assert is_valid_storage_path("receipts/statement-0000.pdf")
    assert not is_valid_storage_path("/data/documents/statement.pdf")
    assert not is_valid_storage_path("../statement.pdf")
    assert not is_valid_storage_path("receipts/../statement.pdf")
    assert not is_valid_storage_path("receipts//statement.pdf")


def test_reconciled_run_must_balance(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "reconcile")
    try:
        doc = seed.rows["document"]["id"]
        acct = seed.rows["account"]["id"]
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            run = c.execute(
                table("import_run")
                .insert()
                .values(
                    household_id=seed.household_id,
                    document_id=doc,
                    account_id=acct,
                    status="succeeded",
                    finished_at=WHEN,
                    reconcile_status="reconciled",
                    opening_balance=Decimal("100.00"),
                    txn_sum=Decimal("0.00"),
                    closing_balance=Decimal("100.01"),
                )
                .returning(table("import_run").c.id)
            ).scalar_one()
            diff = c.execute(
                select(table("import_run").c.balance_diff).where(table("import_run").c.id == run)
            ).scalar_one()
            assert diff == Decimal("0.01")
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="reconciled_within_tolerance"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                table("import_run")
                .insert()
                .values(
                    household_id=seed.household_id,
                    document_id=doc,
                    account_id=acct,
                    status="succeeded",
                    finished_at=WHEN,
                    reconcile_status="reconciled",
                    opening_balance=Decimal("100.00"),
                    txn_sum=Decimal("-40.00"),
                    closing_balance=Decimal("60.02"),
                )
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_needs_review_flags_the_document_until_accepted(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "review")
    doc = table("document")
    run = table("import_run")
    doc_id = seed.rows["document"]["id"]
    try:
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            run_id = c.execute(
                run.insert()
                .values(
                    household_id=seed.household_id,
                    document_id=doc_id,
                    account_id=seed.rows["account"]["id"],
                    reconcile_status="needs_review",
                    opening_balance=Decimal("10.00"),
                    txn_sum=Decimal("1.00"),
                    closing_balance=Decimal("12.00"),
                )
                .returning(run.c.id)
            ).scalar_one()
            status = c.execute(select(doc.c.parse_status).where(doc.c.id == doc_id)).scalar_one()
            assert status == "needs_review"
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="needs review"):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(doc.update().where(doc.c.id == doc_id).values(parse_status="parsed"))
        reviewer = uuid.uuid4()
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                run.update()
                .where(run.c.id == run_id)
                .values(
                    reconcile_status="accepted",
                    reviewed_by_user_id=reviewer,
                    reviewed_at=WHEN,
                )
            )
            latched = c.execute(
                select(doc.c.import_review_latched).where(doc.c.id == doc_id)
            ).scalar_one()
            assert latched is False
            c.execute(doc.update().where(doc.c.id == doc_id).values(parse_status="parsed"))
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="review of import run"):
            set_ctx(c, seed.household_id, wide=True)
            # Same timestamp is not a change. A later one must be refused.
            c.execute(
                run.update()
                .where(run.c.id == run_id)
                .values(reviewed_at=datetime(2026, 2, 1, 12, 0, tzinfo=UTC))
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_accepted_is_not_an_insert_and_reconciliation_is_final(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "final")
    run = table("import_run")
    try:
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="only be accepted after review"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                run.insert().values(
                    household_id=seed.household_id,
                    document_id=seed.rows["document"]["id"],
                    account_id=seed.rows["account"]["id"],
                    reconcile_status="accepted",
                    reviewed_by_user_id=uuid.uuid4(),
                    reviewed_at=WHEN,
                )
            )
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            run_id = c.execute(
                run.insert()
                .values(
                    household_id=seed.household_id,
                    document_id=seed.rows["document"]["id"],
                    account_id=seed.rows["account"]["id"],
                    status="succeeded",
                    finished_at=WHEN,
                    reconcile_status="reconciled",
                    opening_balance=Decimal("50.00"),
                    txn_sum=Decimal("0.00"),
                    closing_balance=Decimal("50.00"),
                )
                .returning(run.c.id)
            ).scalar_one()
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="reconciliation of import run"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                run.update().where(run.c.id == run_id).values(closing_balance=Decimal("49.99"))
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_supersession_stays_one_to_one_inside_the_account(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "supersede")
    txn = table("transaction")
    hid = seed.household_id
    acct = seed.rows["account"]["id"]
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            run_id = _open_run(c, hid, seed.rows["document"]["id"], acct)
            winner = _txn(c, hid, acct, Decimal("-5.00"), "plaid")
            loser = _txn(
                c, hid, acct, Decimal("-5.00"), "import", fingerprint=_fp(1), import_run_id=run_id
            )
            c.execute(txn.update().where(txn.c.id == loser).values(superseded_by_id=winner))
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            other = _txn(
                c, hid, acct, Decimal("-5.00"), "import", fingerprint=_fp(2), import_run_id=run_id
            )
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="uq_transaction_superseded_by_id"),
        ):
            set_ctx(c, hid, wide=True)
            c.execute(txn.update().where(txn.c.id == other).values(superseded_by_id=winner))
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="cannot itself be superseded"),
        ):
            set_ctx(c, hid, wide=True)
            c.execute(txn.update().where(txn.c.id == winner).values(superseded_by_id=other))
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="not_self_superseded"):
            set_ctx(c, hid, wide=True)
            c.execute(txn.update().where(txn.c.id == other).values(superseded_by_id=other))
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            c.execute(txn.delete().where(txn.c.id == winner))
            link = c.execute(select(txn.c.superseded_by_id).where(txn.c.id == loser)).scalar_one()
            assert link is None
    finally:
        delete_household(owner_engine, seed.household_id)


def test_import_run_cannot_be_deleted_while_its_rows_exist(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "undo")
    run = table("import_run")
    txn = table("transaction")
    run_id = seed.rows["import_run"]["id"]
    try:
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            _start(c, run_id)
            row = _txn(
                c,
                seed.household_id,
                seed.rows["account"]["id"],
                Decimal("-8.00"),
                "import",
                fingerprint=_fp(3),
                import_run_id=run_id,
            )
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="fk_transaction_import_run_id"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(run.delete().where(run.c.id == run_id))
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            c.execute(txn.delete().where(txn.c.id == row))
            deleted = c.execute(run.delete().where(run.c.id == run_id))
            assert deleted.rowcount == 1
    finally:
        delete_household(owner_engine, seed.household_id)


def test_household_delete_removes_a_run_and_its_rows(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "cascade")
    try:
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            _start(c, seed.rows["import_run"]["id"])
            _txn(
                c,
                seed.household_id,
                seed.rows["account"]["id"],
                Decimal("-8.00"),
                "import",
                fingerprint=_fp(4),
                import_run_id=seed.rows["import_run"]["id"],
            )
        delete_household(owner_engine, seed.household_id)
        with owner_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            assert count(c, "import_run") == 0
            assert count(c, "transaction") == 0
    finally:
        delete_household(owner_engine, seed.household_id)


def test_private_import_run_follows_the_account(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "private-run")
    owner, other = seed.person_ids
    try:
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            private = c.execute(
                table("account")
                .insert()
                .values(
                    household_id=seed.household_id,
                    owner_person_id=owner,
                    source="manual",
                    name="Example Private",
                    mask="0001",
                    type="depository",
                    visibility="private",
                )
                .returning(table("account").c.id)
            ).scalar_one()
            c.execute(
                table("import_run")
                .insert()
                .values(
                    household_id=seed.household_id,
                    document_id=seed.rows["document"]["id"],
                    account_id=private,
                )
            )
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, other)
            # The shared account's run stays visible. The private account's run does not.
            assert count(c, "import_run") == 1
            hidden = c.execute(
                select(table("import_run").c.id).where(table("import_run").c.account_id == private)
            ).all()
            assert hidden == []
            assert count(c, "sync_run", seed.rows["sync_run"]) == 1
        with app_engine.begin() as c, pytest.raises(DBAPIError, match=RLS_VIOLATION):
            set_ctx(c, seed.household_id, other)
            c.execute(
                table("import_run")
                .insert()
                .values(
                    household_id=seed.household_id,
                    document_id=seed.rows["document"]["id"],
                    account_id=seed.rows["account"]["id"],
                )
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_sync_cursor_is_recorded_only_on_success(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "cursor")
    sync = table("sync_run")
    try:
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="cursor_after_only_on_success"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                sync.update()
                .where(sync.c.id == seed.rows["sync_run"]["id"])
                .values(cursor_after="cursor-not-a-secret")
            )
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                sync.update()
                .where(sync.c.id == seed.rows["sync_run"]["id"])
                .values(status="succeeded", finished_at=WHEN, cursor_after="cursor-page-2")
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_absolute_storage_path_is_rejected(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "path")
    try:
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="storage_path_relative"):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                table("document")
                .insert()
                .values(
                    household_id=seed.household_id,
                    kind="statement",
                    filename="statement.pdf",
                    sha256=_fp(9),
                    storage_path="/data/documents/statement.pdf",
                    mime="application/pdf",
                    size_bytes=10,
                )
            )
    finally:
        delete_household(owner_engine, seed.household_id)


# Twenty zeros: a rejected status value. The database error must not repeat it.
REJECTED_STATUS = "00000000000000000000"


def test_unassigned_run_holds_no_transaction_sum(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "unassigned")
    run = table("import_run")
    try:
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                run.insert().values(
                    household_id=seed.household_id,
                    document_id=seed.rows["document"]["id"],
                )
            )
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="balances_need_account"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                run.insert().values(
                    household_id=seed.household_id,
                    document_id=seed.rows["document"]["id"],
                    txn_sum=Decimal("1.00"),
                )
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_succeeded_run_cannot_stay_pending(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "succeeded")
    try:
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="succeeded_is_reconciled"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                table("import_run")
                .insert()
                .values(
                    household_id=seed.household_id,
                    document_id=seed.rows["document"]["id"],
                    account_id=seed.rows["account"]["id"],
                    status="succeeded",
                    finished_at=WHEN,
                    reconcile_status="pending",
                )
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_import_run_link_cannot_be_cleared(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "link")
    txn = table("transaction")
    run_id = seed.rows["import_run"]["id"]
    try:
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            _start(c, run_id)
            row = _txn(
                c,
                seed.household_id,
                seed.rows["account"]["id"],
                Decimal("-8.00"),
                "import",
                fingerprint=_fp(5),
                import_run_id=run_id,
            )
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="import run link is final"),
        ):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(txn.update().where(txn.c.id == row).values(import_run_id=None))
    finally:
        delete_household(owner_engine, seed.household_id)


def test_deleting_a_flagged_run_leaves_the_document_latched(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "latch")
    doc = table("document")
    run = table("import_run")
    doc_id = seed.rows["document"]["id"]
    run_id = seed.rows["import_run"]["id"]
    try:
        with app_engine.begin() as c:
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                run.update().where(run.c.id == run_id).values(reconcile_status="needs_review")
            )
            c.execute(run.delete().where(run.c.id == run_id))
            latched = c.execute(
                select(doc.c.import_review_latched).where(doc.c.id == doc_id)
            ).scalar_one()
            assert latched is True
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="needs review"):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(doc.update().where(doc.c.id == doc_id).values(parse_status="parsed"))
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="review clears only"):
            set_ctx(c, seed.household_id, wide=True)
            c.execute(doc.update().where(doc.c.id == doc_id).values(import_review_latched=False))
    finally:
        delete_household(owner_engine, seed.household_id)


def test_reconcile_status_error_does_not_echo_the_value(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "echo")
    run = table("import_run")
    try:
        with app_engine.begin() as c, pytest.raises(IntegrityError) as caught:
            set_ctx(c, seed.household_id, wide=True)
            c.execute(
                run.update()
                .where(run.c.id == seed.rows["import_run"]["id"])
                .values(reconcile_status=REJECTED_STATUS)
            )
        message = _db_message(caught.value)
        assert "cannot change" in message
        assert REJECTED_STATUS not in message
    finally:
        delete_household(owner_engine, seed.household_id)


def _db_message(exc: IntegrityError) -> str:
    """The database diagnostic only. The statement parameters are the write, not the error."""
    diag = getattr(exc.orig, "diag", None)
    if diag is None:
        return str(exc.orig)
    parts = (diag.message_primary, diag.message_detail, diag.context, diag.internal_query)
    return " ".join(part for part in parts if part)


def _txn(
    conn: Connection,
    household_id: uuid.UUID,
    account_id: uuid.UUID,
    amount: Decimal,
    source: str,
    *,
    fingerprint: str | None = None,
    import_run_id: uuid.UUID | None = None,
) -> uuid.UUID:
    txn = table("transaction")
    return conn.execute(
        txn.insert()
        .values(
            household_id=household_id,
            account_id=account_id,
            posted_date=date(2026, 1, 15),
            amount=amount,
            description_raw="EXAMPLE MART #0000",
            source=source,
            import_fingerprint=fingerprint,
            import_run_id=import_run_id,
        )
        .returning(txn.c.id)
    ).scalar_one()


def _start(conn: Connection, run_id: uuid.UUID) -> None:
    run = table("import_run")
    conn.execute(run.update().where(run.c.id == run_id).values(status="running", started_at=WHEN))


def _open_run(
    conn: Connection,
    household_id: uuid.UUID,
    document_id: uuid.UUID,
    account_id: uuid.UUID | None,
) -> uuid.UUID:
    """A running, unreconciled run: the only kind a row may join."""
    run = table("import_run")
    return conn.execute(
        run.insert()
        .values(
            household_id=household_id,
            document_id=document_id,
            account_id=account_id,
            status="running",
            started_at=WHEN,
        )
        .returning(run.c.id)
    ).scalar_one()


def _account(
    conn: Connection,
    household_id: uuid.UUID,
    owner: uuid.UUID | None,
    visibility: str,
    mask: str,
) -> uuid.UUID:
    acct = table("account")
    return conn.execute(
        acct.insert()
        .values(
            household_id=household_id,
            owner_person_id=owner,
            source="manual",
            name=f"Example {mask}",
            mask=mask,
            type="depository",
            visibility=visibility,
        )
        .returning(acct.c.id)
    ).scalar_one()


def _document(conn: Connection, household_id: uuid.UUID, person_id: uuid.UUID | None) -> uuid.UUID:
    doc = table("document")
    return conn.execute(
        doc.insert()
        .values(
            household_id=household_id,
            person_id=person_id,
            kind="statement",
            filename="statement.pdf",
            sha256=uuid.uuid4().hex * 2,
            storage_path=f"statements/{uuid.uuid4()}.pdf",
            mime="application/pdf",
            size_bytes=10,
        )
        .returning(doc.c.id)
    ).scalar_one()


def _undo(conn: Connection, run_id: uuid.UUID) -> int:
    """What the import service's undo does: the run's rows (under RLS), then the run."""
    txn = table("transaction")
    run = table("import_run")
    rows = conn.execute(txn.delete().where(txn.c.import_run_id == run_id)).rowcount
    conn.execute(run.delete().where(run.c.id == run_id))
    return rows


# --------------------------------------------------------------- reconciliation can't be bypassed


def test_imported_row_needs_a_run(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "no-run")
    try:
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="import_has_run"):
            set_ctx(c, seed.household_id, wide=True)
            _txn(
                c,
                seed.household_id,
                seed.rows["account"]["id"],
                Decimal("-999.00"),
                "import",
                fingerprint=_fp(90),
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_rows_join_only_a_running_unreconciled_run(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "closed-run")
    hid = seed.household_id
    acct = seed.rows["account"]["id"]
    doc = seed.rows["document"]["id"]
    run = table("import_run")
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            reconciled = c.execute(
                run.insert()
                .values(
                    household_id=hid,
                    document_id=doc,
                    account_id=acct,
                    status="succeeded",
                    finished_at=WHEN,
                    reconcile_status="reconciled",
                    opening_balance=Decimal("5.00"),
                    txn_sum=Decimal("-1.00"),
                    closing_balance=Decimal("4.00"),
                )
                .returning(run.c.id)
            ).scalar_one()
            flagged = _open_run(c, hid, doc, acct)
            c.execute(
                run.update().where(run.c.id == flagged).values(reconcile_status="needs_review")
            )
        # A finished, reconciled run; a queued run; a running run already flagged.
        for n, run_id in enumerate((reconciled, seed.rows["import_run"]["id"], flagged)):
            with app_engine.begin() as c, pytest.raises(IntegrityError, match="is not running"):
                set_ctx(c, hid, wide=True)
                _txn(
                    c,
                    hid,
                    acct,
                    Decimal("-500.00"),
                    "import",
                    fingerprint=_fp(91 + n),
                    import_run_id=run_id,
                )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_failed_run_with_rows_is_flagged_and_latches_the_document(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "crash")
    hid = seed.household_id
    acct = seed.rows["account"]["id"]
    run = table("import_run")
    doc = table("document")
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            # A run that fails before landing a row stays pending and flags nothing.
            empty_doc = _document(c, hid, None)
            empty = _open_run(c, hid, empty_doc, acct)
            c.execute(
                run.update()
                .where(run.c.id == empty)
                .values(status="failed", finished_at=WHEN, error_class="ValueError")
            )
            state = c.execute(
                select(doc.c.parse_status, doc.c.import_review_latched).where(doc.c.id == empty_doc)
            ).one()
            assert tuple(state) == ("pending", False)
            assert (
                c.execute(select(run.c.reconcile_status).where(run.c.id == empty)).scalar_one()
                == "pending"
            )

            crashed_doc = _document(c, hid, None)
            crashed = _open_run(c, hid, crashed_doc, acct)
            _txn(
                c,
                hid,
                acct,
                Decimal("-42.00"),
                "import",
                fingerprint=_fp(92),
                import_run_id=crashed,
            )
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            c.execute(
                run.update()
                .where(run.c.id == crashed)
                .values(
                    status="failed",
                    finished_at=WHEN,
                    error_class="ValueError",
                    rows_parsed=1,
                    rows_inserted=1,
                )
            )
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            status = c.execute(
                select(run.c.status, run.c.reconcile_status).where(run.c.id == crashed)
            ).one()
            assert tuple(status) == ("failed", "needs_review")
            state = c.execute(
                select(doc.c.parse_status, doc.c.import_review_latched).where(
                    doc.c.id == crashed_doc
                )
            ).one()
            assert tuple(state) == ("needs_review", True)
    finally:
        delete_household(owner_engine, seed.household_id)


def test_run_with_rows_keeps_its_account_and_document(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "rows-final")
    hid = seed.household_id
    acct = seed.rows["account"]["id"]
    run = table("import_run")
    txn = table("transaction")
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            other_acct = _account(c, hid, None, "shared", "0009")
            other_doc = _document(c, hid, None)
            run_id = _open_run(c, hid, seed.rows["document"]["id"], acct)
            row = _txn(
                c, hid, acct, Decimal("-3.00"), "import", fingerprint=_fp(93), import_run_id=run_id
            )
        for values in ({"account_id": other_acct}, {"document_id": other_doc}):
            with (
                app_engine.begin() as c,
                pytest.raises(IntegrityError, match="account and document are final"),
            ):
                set_ctx(c, hid, wide=True)
                c.execute(run.update().where(run.c.id == run_id).values(**values))
        with (
            app_engine.begin() as c,
            pytest.raises(IntegrityError, match="its account is final"),
        ):
            set_ctx(c, hid, wide=True)
            c.execute(txn.update().where(txn.c.id == row).values(account_id=other_acct))
        with app_engine.begin() as c:
            # Rewriting the row with the same link (an ORM full-row update) is not a change.
            set_ctx(c, hid, wide=True)
            c.execute(
                txn.update()
                .where(txn.c.id == row)
                .values(import_run_id=run_id, account_id=acct, merchant_name="Example Mart")
            )
    finally:
        delete_household(owner_engine, seed.household_id)


def test_reconcile_waits_for_a_row_joining_the_run(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "join-lock")
    hid = seed.household_id
    acct = seed.rows["account"]["id"]
    run = table("import_run")
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            run_id = _open_run(c, hid, seed.rows["document"]["id"], acct)
        with app_engine.connect() as joining, joining.begin():
            set_ctx(joining, hid, wide=True)
            _txn(
                joining,
                hid,
                acct,
                Decimal("-7.00"),
                "import",
                fingerprint=_fp(94),
                import_run_id=run_id,
            )
            # The joining row holds the run until it commits, so the reconcile can't slip in
            # between the guard's check and the insert.
            with app_engine.begin() as c, pytest.raises(DBAPIError, match="lock timeout"):
                set_ctx(c, hid, wide=True)
                c.exec_driver_sql("SET LOCAL lock_timeout = '200ms'")
                c.execute(
                    run.update()
                    .where(run.c.id == run_id)
                    .values(
                        reconcile_status="reconciled",
                        opening_balance=Decimal("10.00"),
                        txn_sum=Decimal("0.00"),
                        closing_balance=Decimal("10.00"),
                    )
                )
    finally:
        delete_household(owner_engine, seed.household_id)


# ------------------------------------------------------------ one person can't use another's run


def test_person_cannot_link_rows_into_another_persons_private_run(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "private-link")
    hid = seed.household_id
    alex, sam = seed.person_ids
    txn = table("transaction")
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            alex_private = _account(c, hid, alex, "private", "0001")
            sam_private = _account(c, hid, sam, "private", "0002")
            joint = _account(c, hid, None, "shared", "0003")
            alex_doc = _document(c, hid, alex)
        with app_engine.begin() as c:
            set_ctx(c, hid, alex)
            alex_run = _open_run(c, hid, alex_doc, alex_private)
            alex_row = _txn(
                c,
                hid,
                alex_private,
                Decimal("-10.00"),
                "import",
                fingerprint=_fp(1),
                import_run_id=alex_run,
            )
        for n, acct in enumerate((joint, sam_private)):
            with app_engine.begin() as c, pytest.raises(IntegrityError, match="not open"):
                set_ctx(c, hid, sam)
                _txn(
                    c,
                    hid,
                    acct,
                    Decimal("-1.00"),
                    "import",
                    fingerprint=_fp(70 + n),
                    import_run_id=alex_run,
                )
        with app_engine.begin() as c:
            set_ctx(c, hid, alex)
            assert _undo(c, alex_run) == 1
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            assert count(c, "import_run", {"id": alex_run}) == 0
            assert count(c, "transaction", {"id": alex_row}) == 0
            assert count(c, "transaction", {"import_run_id": alex_run}) == 0
            assert c.execute(select(txn.c.id).where(txn.c.account_id == sam_private)).all() == []
    finally:
        delete_household(owner_engine, seed.household_id)


def test_joint_account_rows_cannot_join_another_persons_run(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "joint-link")
    hid = seed.household_id
    alex, sam = seed.person_ids
    txn = table("transaction")
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            joint = _account(c, hid, None, "shared", "0003")
            alex_private = _account(c, hid, alex, "private", "0001")
            alex_doc = _document(c, hid, alex)
        with app_engine.begin() as c:
            set_ctx(c, hid, alex)
            # Alex's statement for the joint account: Sam can see this run, not write it.
            joint_run = _open_run(c, hid, alex_doc, joint)
            private_run = _open_run(c, hid, alex_doc, alex_private)
            _txn(
                c,
                hid,
                joint,
                Decimal("-4.00"),
                "import",
                fingerprint=_fp(1),
                import_run_id=joint_run,
            )
        with app_engine.begin() as c:
            set_ctx(c, hid, sam)
            sam_row = _txn(c, hid, joint, Decimal("-2.00"), "manual")
            assert count(c, "import_run", {"id": joint_run}) == 1
        for run_id in (joint_run, private_run):
            with app_engine.begin() as c, pytest.raises(IntegrityError, match="not open"):
                set_ctx(c, hid, sam)
                _txn(
                    c,
                    hid,
                    joint,
                    Decimal("-2.00"),
                    "import",
                    fingerprint=_fp(2),
                    import_run_id=run_id,
                )
        # An existing row can't be pulled into a run later, by anyone.
        for person, wide in ((sam, False), (None, True)):
            with app_engine.begin() as c, pytest.raises(IntegrityError, match="link is final"):
                set_ctx(c, hid, person, wide=wide)
                c.execute(
                    txn.update()
                    .where(txn.c.id == sam_row)
                    .values(source="import", import_fingerprint=_fp(3), import_run_id=joint_run)
                )
        with app_engine.begin() as c:
            set_ctx(c, hid, alex)
            assert _undo(c, joint_run) == 1
            assert _undo(c, private_run) == 0
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            assert count(c, "transaction", {"id": sam_row}) == 1
    finally:
        delete_household(owner_engine, seed.household_id)


def test_hidden_run_and_missing_run_fail_the_same_way(
    app_engine: Engine, owner_engine: Engine
) -> None:
    seed = seed_household(app_engine, "oracle")
    other = seed_household(app_engine, "oracle-other")
    hid = seed.household_id
    alex, sam = seed.person_ids
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            alex_private = _account(c, hid, alex, "private", "0001")
            sam_private = _account(c, hid, sam, "private", "0002")
            alex_run = _open_run(c, hid, _document(c, hid, alex), alex_private)
        with app_engine.begin() as c:
            set_ctx(c, other.household_id, wide=True)
            _start(c, other.rows["import_run"]["id"])
        outcomes = []
        for n, run_id in enumerate((uuid.uuid4(), alex_run, other.rows["import_run"]["id"])):
            with app_engine.begin() as c, pytest.raises(IntegrityError) as caught:
                set_ctx(c, hid, sam)
                _txn(
                    c,
                    hid,
                    sam_private,
                    Decimal("-1.00"),
                    "import",
                    fingerprint=_fp(80 + n),
                    import_run_id=run_id,
                )
            sqlstate = getattr(caught.value.orig, "sqlstate", None)
            outcomes.append((sqlstate, _db_message(caught.value).replace(str(run_id), "<run>")))
        assert outcomes[0][0] == "23503"
        assert outcomes[0] == outcomes[1] == outcomes[2]
        assert "fk_transaction_import_run_id" not in outcomes[0][1]
    finally:
        delete_household(owner_engine, seed.household_id)
        delete_household(owner_engine, other.household_id)


def test_runs_follow_their_document(app_engine: Engine, owner_engine: Engine) -> None:
    seed = seed_household(app_engine, "doc-run")
    hid = seed.household_id
    alex, sam = seed.person_ids
    run = table("import_run")
    doc = table("document")
    try:
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            joint = _account(c, hid, None, "shared", "0003")
            alex_doc = _document(c, hid, alex)
            household_doc = _document(c, hid, None)
            alex_export = _open_run(c, hid, alex_doc, None)
        # Sam can't start, flag or accept a run on Alex's statement, with or without an account.
        for acct in (None, joint):
            with app_engine.begin() as c, pytest.raises(DBAPIError, match=RLS_VIOLATION):
                set_ctx(c, hid, sam)
                c.execute(
                    run.insert().values(
                        household_id=hid,
                        document_id=alex_doc,
                        account_id=acct,
                        reconcile_status="needs_review",
                    )
                )
        with app_engine.begin() as c:
            set_ctx(c, hid, sam)
            flagged = c.execute(
                run.update().where(run.c.id == alex_export).values(reconcile_status="needs_review")
            )
            assert flagged.rowcount == 0
        # Nor add rows to Alex's multi-account export, even in an account Sam may write.
        with app_engine.begin() as c, pytest.raises(IntegrityError, match="not open"):
            set_ctx(c, hid, sam)
            _txn(
                c,
                hid,
                joint,
                Decimal("-1.00"),
                "import",
                fingerprint=_fp(1),
                import_run_id=alex_export,
            )
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            state = c.execute(
                select(doc.c.parse_status, doc.c.import_review_latched).where(doc.c.id == alex_doc)
            ).one()
            assert tuple(state) == ("pending", False)
        # A household statement is the household's: Sam may run it.
        with app_engine.begin() as c:
            set_ctx(c, hid, sam)
            household_run = _open_run(c, hid, household_doc, None)
            _txn(
                c,
                hid,
                joint,
                Decimal("-1.00"),
                "import",
                fingerprint=_fp(2),
                import_run_id=household_run,
            )
    finally:
        delete_household(owner_engine, seed.household_id)
