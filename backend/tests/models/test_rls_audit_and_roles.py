"""Append-only audit log, create_household(), grants and the app role's lack of DDL."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from tests.models.conftest import (
    HOUSEHOLD_TABLES,
    IDENTITY_TABLES,
    REFERENCE_TABLES,
    Seed,
    count,
    delete_household,
    set_ctx,
    table,
)

pytestmark = pytest.mark.services

DML = {"SELECT", "INSERT", "UPDATE", "DELETE"}


def _audit(household_id: uuid.UUID | None, action: str) -> dict[str, object]:
    return {"id": uuid.uuid4(), "household_id": household_id, "action": action}


# --------------------------------------------------------------------------- audit_log


def test_audit_rows_cannot_be_updated_or_deleted(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    row = _audit(a.household_id, "settings.change")
    with app_engine.begin() as c:
        set_ctx(c, a.household_id)
        c.execute(table("audit_log").insert().values(**row))
        assert count(c, "audit_log", {"id": row["id"]}) == 1
    for stmt in (
        "UPDATE audit_log SET action = 'tampered' WHERE id = :id",
        "DELETE FROM audit_log WHERE id = :id",
        "TRUNCATE audit_log",
    ):
        with app_engine.begin() as c:
            set_ctx(c, a.household_id)
            with pytest.raises(DBAPIError, match="permission denied"):
                c.execute(text(stmt), {"id": row["id"]})


def test_pre_auth_audit_insert_with_null_household(app_engine: Engine) -> None:
    """Failed logins are recorded before any household is known, and can't be read back."""
    row = _audit(None, "login.failed")
    with app_engine.begin() as c:
        c.execute(table("audit_log").insert().values(**row, detail={"reason": "bad_assertion"}))
        assert count(c, "audit_log", {"id": row["id"]}) == 0


def test_audit_insert_for_other_household_is_rejected(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, b = two_households
    with app_engine.begin() as c:
        set_ctx(c, a.household_id)
        with pytest.raises(DBAPIError, match="row-level security"):
            c.execute(table("audit_log").insert().values(**_audit(b.household_id, "export")))


def test_audit_rows_are_household_scoped(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, b = two_households
    row_b = _audit(b.household_id, "export")
    with app_engine.begin() as c:
        set_ctx(c, b.household_id)
        c.execute(table("audit_log").insert().values(**row_b))
    with app_engine.begin() as c:
        set_ctx(c, a.household_id)
        assert count(c, "audit_log", {"id": row_b["id"]}) == 0
        hh = c.execute(text("SELECT DISTINCT household_id FROM audit_log")).scalars().all()
        assert set(hh) <= {a.household_id}


# --------------------------------------------------------------------------- create_household


def test_create_household_works_without_context(app_engine: Engine) -> None:
    with app_engine.begin() as c:
        hid = c.execute(text("SELECT create_household('Example Setup')")).scalar_one()
        assert isinstance(hid, uuid.UUID)
        # The caller's (empty) context is restored: the new row isn't visible yet.
        assert c.execute(text("SELECT app_household_id()")).scalar_one() is None
        assert count(c, "household") == 0
        set_ctx(c, hid)
        assert c.execute(text("SELECT name FROM household")).scalar_one() == "Example Setup"
    delete_household(app_engine, hid)


def test_create_household_restores_existing_context(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    with app_engine.begin() as c:
        set_ctx(c, a.household_id)
        hid = c.execute(text("SELECT create_household('Example Second')")).scalar_one()
        assert c.execute(text("SELECT app_household_id()")).scalar_one() == a.household_id
    delete_household(app_engine, hid)


def test_create_household_rejects_blank_name(app_engine: Engine) -> None:
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="household name is required"):
        c.execute(text("SELECT create_household('   ')"))


def test_create_household_is_security_definer_with_pinned_search_path(
    owner_engine: Engine,
) -> None:
    with owner_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT p.prosecdef, p.proconfig, pg_get_userbyid(p.proowner),"
                " has_function_privilege('holdfast_app', p.oid, 'EXECUTE'),"
                " has_function_privilege('public', p.oid, 'EXECUTE')"
                " FROM pg_proc p WHERE p.proname = 'create_household'"
            )
        ).one()
    secdef, config, owner, app_exec, public_exec = row
    assert secdef is True
    assert config == ["search_path=pg_catalog, pg_temp"]
    assert owner == "holdfast_owner"
    assert app_exec is True
    assert public_exec is False


def test_household_delete_cascades_to_every_table(app_engine: Engine) -> None:
    from tests.models.conftest import seed_household

    seed = seed_household(app_engine, "Cascade")
    delete_household(app_engine, seed.household_id)
    with app_engine.begin() as c:
        set_ctx(c, seed.household_id, wide=True)
        for name in HOUSEHOLD_TABLES:
            assert count(c, name) == 0, name


# --------------------------------------------------------------------------- grants and DDL


def _privileges(engine: Engine, name: str) -> set[str]:
    with engine.connect() as c:
        return {
            p
            for p in (*DML, "TRUNCATE", "REFERENCES", "TRIGGER")
            if c.execute(
                text("SELECT has_table_privilege('holdfast_app', :t, :p)"),
                {"t": f'public."{name}"', "p": p},
            ).scalar_one()
        }


@pytest.mark.parametrize("name", (*HOUSEHOLD_TABLES, *IDENTITY_TABLES))
def test_app_has_dml_only(owner_engine: Engine, name: str) -> None:
    assert _privileges(owner_engine, name) == DML


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("household", {"SELECT", "UPDATE", "DELETE"}),
        ("audit_log", {"SELECT", "INSERT"}),
        *[(t, {"SELECT", "INSERT", "UPDATE"}) for t in REFERENCE_TABLES],
    ],
)
def test_narrowed_grants(owner_engine: Engine, name: str, expected: set[str]) -> None:
    assert _privileges(owner_engine, name) == expected


@pytest.mark.parametrize(
    "ddl",
    [
        "CREATE TABLE _holdfast_ddl_probe (id int)",
        "ALTER TABLE account DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE account NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY household_isolation ON account",
        "ALTER TABLE account ADD COLUMN probe text",
        "CREATE OR REPLACE FUNCTION app_household_id() RETURNS uuid LANGUAGE sql"
        " AS $$ SELECT NULL::uuid $$",
        "DROP TABLE audit_log",
    ],
)
def test_app_cannot_run_ddl(app_engine: Engine, ddl: str) -> None:
    with (
        app_engine.begin() as c,
        pytest.raises(DBAPIError, match=r"permission denied|must be owner"),
    ):
        c.execute(text(ddl))
