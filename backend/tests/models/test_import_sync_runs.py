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
            winner = _txn(c, hid, acct, Decimal("-5.00"), "plaid")
            loser = _txn(c, hid, acct, Decimal("-5.00"), "import", fingerprint=_fp(1))
            c.execute(txn.update().where(txn.c.id == loser).values(superseded_by_id=winner))
        with app_engine.begin() as c:
            set_ctx(c, hid, wide=True)
            other = _txn(c, hid, acct, Decimal("-5.00"), "import", fingerprint=_fp(2))
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
