"""Postgres role split: RLS only protects anything if the runtime role can't bypass it.

The runtime role (TEST_DATABASE_URL, holdfast_app) and the migration role
(TEST_MIGRATIONS_DATABASE_URL, holdfast_owner) are both checked. These run in CI (no skip).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.services


@contextmanager
def _connect(url: str) -> Iterator[Connection]:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def _role_flags(url: str) -> tuple[str, bool, bool]:
    with _connect(url) as conn:
        row = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
    return str(row[0]), bool(row[1]), bool(row[2])


def test_runtime_role_is_not_superuser_and_cannot_bypass_rls(database_url: str) -> None:
    name, rolsuper, rolbypassrls = _role_flags(database_url)
    assert name == "holdfast_app"
    assert rolsuper is False
    assert rolbypassrls is False


def test_runtime_role_cannot_create_in_public(database_url: str) -> None:
    with _connect(database_url) as conn:
        can_create = conn.execute(
            text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
        ).scalar_one()
    assert can_create is False


def test_migration_role_is_owner_but_not_superuser(migrations_database_url: str) -> None:
    name, rolsuper, rolbypassrls = _role_flags(migrations_database_url)
    assert name == "holdfast_owner"
    assert rolsuper is False
    assert rolbypassrls is False


def test_owner_tables_are_usable_by_runtime_role(
    database_url: str, migrations_database_url: str
) -> None:
    """ALTER DEFAULT PRIVILEGES: tables the owner creates get DML (not DDL) for the app role."""
    with _connect(migrations_database_url) as owner:
        owner.execute(text("DROP TABLE IF EXISTS _holdfast_privcheck"))
        owner.execute(text("CREATE TABLE _holdfast_privcheck (id serial PRIMARY KEY, note text)"))
        owner.commit()
    try:
        with _connect(database_url) as app:
            app.execute(text("INSERT INTO _holdfast_privcheck (note) VALUES ('synthetic')"))
            assert app.execute(text("SELECT count(*) FROM _holdfast_privcheck")).scalar_one() == 1
            app.rollback()
            with pytest.raises(ProgrammingError, match="must be owner"):
                app.execute(text("DROP TABLE _holdfast_privcheck"))
    finally:
        with _connect(migrations_database_url) as owner:
            owner.execute(text("DROP TABLE IF EXISTS _holdfast_privcheck"))
            owner.commit()
