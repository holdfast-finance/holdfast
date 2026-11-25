"""0012's owner-only up/down/up and both non-empty guards."""

from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from tests.models.conftest import alembic_config, delete_household, set_ctx

PARENT = "0011_connector_link"
REVISION = "0012_rules"


@pytest.mark.services
def test_rules_migration_round_trip_and_refuses_to_lose_rows(
    migrations_database_url: str,
) -> None:
    cfg = alembic_config(migrations_database_url)
    engine = create_engine(migrations_database_url)
    command.upgrade(cfg, REVISION)
    command.downgrade(cfg, PARENT)
    hid = None
    try:
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT current_user")) == "holdfast_owner"
            hid = conn.scalar(text("SELECT create_household('Example migration household')"))
            set_ctx(conn, hid, wide=True)
            conn.execute(
                text("""INSERT INTO rule (household_id, match, "set")
                                 VALUES (:h, '{}', '{}')"""),
                {"h": hid},
            )
        with pytest.raises(RuntimeError, match=r"cannot upgrade 0012: rule contains 1 row"):
            command.upgrade(cfg, REVISION)
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == PARENT
            set_ctx(conn, hid, wide=True)
            assert conn.scalar(text("SELECT count(*) FROM rule")) == 1
            conn.execute(text("DELETE FROM rule"))

        command.upgrade(cfg, REVISION)
        with engine.begin() as conn:
            set_ctx(conn, hid, wide=True)
            conn.execute(
                text("""
                    WITH author AS (
                        INSERT INTO person (household_id, display_name, filing_role)
                        VALUES (:h, 'Alex Example', 'primary') RETURNING id
                    ), category AS (
                        INSERT INTO category (household_id, slug, name, kind)
                        VALUES (:h, 'example', 'Example', 'expense') RETURNING id
                    )
                    INSERT INTO rule (household_id, author_person_id, category_id, name, conditions)
                    SELECT :h, author.id, category.id, 'Example rule', '{"v": 1}'
                    FROM author CROSS JOIN category
                """),
                {"h": hid},
            )
        with pytest.raises(RuntimeError, match=r"cannot downgrade 0012: rule contains 1 row"):
            command.downgrade(cfg, PARENT)
        with engine.begin() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'rule'::regclass")
            )
            set_ctx(conn, hid, wide=True)
            assert conn.scalar(text("SELECT conditions FROM rule")) == {"v": 1}
        delete_household(engine, hid)
        hid = None

        command.downgrade(cfg, PARENT)
        command.upgrade(cfg, REVISION)
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT version_num FROM alembic_version")) == REVISION
            assert conn.scalar(
                text("SELECT relforcerowsecurity FROM pg_class WHERE oid = 'rule'::regclass")
            )
    finally:
        if hid is not None:
            delete_household(engine, hid)
        engine.dispose()
