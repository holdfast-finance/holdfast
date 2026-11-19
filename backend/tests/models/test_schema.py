"""Schema shape after migrations: tables, indexes, RLS flags, and the 0002 round trip."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text

from tests.models.conftest import (
    CONTRACT_TABLES,
    HOUSEHOLD_TABLES,
    IDENTITY_TABLES,
    REFERENCE_TABLES,
    alembic_config,
)

pytestmark = pytest.mark.services

KEY_INDEXES = {
    "ix_transaction_account_id_posted_date": ("transaction", "(account_id, posted_date)"),
    "ix_transaction_category_id_posted_date": ("transaction", "(category_id, posted_date)"),
    "ix_transaction_merchant_name": ("transaction", "(merchant_name)"),
    "ix_transaction_description_raw_trgm": ("transaction", "gin (description_raw gin_trgm_ops)"),
    "ix_balance_snapshot_as_of_date": ("balance_snapshot", "(as_of_date)"),
}
RLS_FUNCTIONS = ("app_household_id", "app_person_id", "app_household_wide", "create_household")


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars()
        )


def test_every_contract_table_exists(owner_engine: Engine) -> None:
    assert set(CONTRACT_TABLES) <= _tables(owner_engine)


def test_key_indexes_exist(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        rows = dict(
            c.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'")
            ).all()
        )
    for name, (tbl, fragment) in KEY_INDEXES.items():
        assert name in rows, name
        assert f"ON public.{tbl} USING" in rows[name].replace('"', "")
        assert fragment in rows[name], rows[name]


def test_every_foreign_key_is_indexed(owner_engine: Engine) -> None:
    """Each FK's referencing column leads some index (PK, unique or plain).

    Composite FKs ``(parent_id, household_id)`` are served by an index on ``parent_id``: the id
    alone is unique, so household_id adds nothing to the lookup.
    """
    with owner_engine.connect() as c:
        missing = c.execute(
            text(
                """
                SELECT con.conrelid::regclass::text, con.conname
                FROM pg_constraint con
                WHERE con.contype = 'f' AND con.connamespace = 'public'::regnamespace
                  AND NOT EXISTS (
                    SELECT 1 FROM pg_index i
                    WHERE i.indrelid = con.conrelid AND i.indkey[0] = con.conkey[1]
                  )
                """
            )
        ).all()
    assert missing == []


def test_every_household_table_has_household_id_index(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        for name in HOUSEHOLD_TABLES:
            leading = c.execute(
                text(
                    "SELECT count(*) FROM pg_index i JOIN pg_attribute a"
                    " ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0]"
                    " WHERE i.indrelid = CAST(:t AS regclass) AND a.attname = 'household_id'"
                ),
                {"t": f'public."{name}"'},
            ).scalar_one()
            assert leading >= 1, name


def test_rls_enabled_and_forced_where_expected(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        flags = {
            r[0]: (r[1], r[2])
            for r in c.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
                )
            )
        }
        with_household_id = set(
            c.execute(
                text(
                    "SELECT table_name FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND column_name = 'household_id'"
                )
            ).scalars()
        )
        policies = set(
            c.execute(text("SELECT tablename FROM pg_policies WHERE schemaname = 'public'"))
            .scalars()
            .all()
        )
    # Any table that carries household_id must be protected (catches future tables too).
    for name in (*HOUSEHOLD_TABLES, "household", "audit_log", *with_household_id):
        assert flags[name] == (True, True), name
        assert name in policies, name
    for name in (*IDENTITY_TABLES, *REFERENCE_TABLES):
        assert flags[name] == (False, False), name


def test_no_float_or_sensitive_columns(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        floats = c.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND data_type IN ('real', 'double precision')"
            )
        ).all()
        sensitive = c.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = 'public'"
                " AND (column_name ~ 'ssn|social_security|tax_id|routing'"
                "      OR column_name ~ '^account_number$|^full_account')"
            )
        ).all()
    assert floats == []
    assert sensitive == []


def test_mask_columns_reject_full_account_numbers(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        defs = (
            c.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conname IN ('ck_account_mask_masked',"
                    " 'ck_tradeline_account_number_masked_masked')"
                )
            )
            .scalars()
            .all()
        )
    assert len(defs) == 2
    assert all("[0-9]{5,}" in d for d in defs)


def test_core_schema_round_trip(owner_url: str) -> None:
    """0002 upgrade -> downgrade -> upgrade leaves nothing behind and rebuilds cleanly."""
    cfg = alembic_config(owner_url)
    engine = create_engine(owner_url)
    try:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0001_baseline")
        assert _tables(engine) <= {"alembic_version"}
        with engine.connect() as c:
            left = c.execute(
                text("SELECT proname FROM pg_proc WHERE proname = ANY(:names)"),
                {"names": list(RLS_FUNCTIONS)},
            ).all()
        assert left == []
        command.upgrade(cfg, "head")
        assert set(CONTRACT_TABLES) <= _tables(engine)
    finally:
        engine.dispose()
