"""Alembic: upgrade -> downgrade -> upgrade against a real Postgres."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parent.parent


def _config(database_url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = database_url
    cfg.attributes["skip_logging_config"] = True
    return cfg


def _extensions(database_url: str) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm','pgcrypto')")
            )
            return {r[0] for r in rows}
    finally:
        engine.dispose()


def test_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert script.get_heads() == ["0008_category_tree"]


@pytest.mark.services
def test_upgrade_downgrade_upgrade(migrations_database_url: str) -> None:
    """Runs as the schema owner (not a superuser), like production migrations."""
    url = migrations_database_url
    cfg = _config(url)
    command.downgrade(cfg, "base")  # start clean even if a previous run left state
    command.upgrade(cfg, "head")
    assert _extensions(url) == {"pg_trgm", "pgcrypto"}
    command.downgrade(cfg, "base")
    assert _extensions(url) == set()
    command.upgrade(cfg, "head")
    assert _extensions(url) == {"pg_trgm", "pgcrypto"}


@pytest.mark.services
def test_category_migration_seeds_existing_households_and_keeps_legacy_rows(
    migrations_database_url: str,
) -> None:
    """0008 over data: a household with a pre-slug category gets the tree, nothing is lost, and
    down then up again neither duplicates nor drops the household's own category."""
    from app.domain.categories import default_categories

    url = migrations_database_url
    cfg = _config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0007_transaction_live_index")
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            hid = c.execute(
                text("SELECT create_household('Example Migration Household')")
            ).scalar_one()
            c.execute(text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
            c.execute(
                text(
                    "INSERT INTO category (household_id, name, kind) VALUES (:h, 'Mine', 'expense')"
                ),
                {"h": hid},
            )

        def state() -> tuple[int, int, list[str]]:
            with engine.begin() as c:
                c.execute(text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
                total = c.execute(text("SELECT count(*) FROM category")).scalar_one()
                system = c.execute(
                    text("SELECT count(*) FROM category WHERE origin = 'system'")
                ).scalar_one()
                legacy = [
                    r[0]
                    for r in c.execute(
                        text("SELECT name FROM category WHERE origin = 'user' ORDER BY name")
                    )
                ]
            return total, system, legacy

        n = len(default_categories())
        command.upgrade(cfg, "head")
        assert state() == (n + 1, n, ["Mine"])
        command.downgrade(cfg, "-1")
        with engine.begin() as c:
            c.execute(text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
            assert c.execute(text("SELECT count(*) FROM category")).scalar_one() == 1
        command.upgrade(cfg, "head")
        assert state() == (n + 1, n, ["Mine"])
        with engine.begin() as c:
            c.execute(text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
            c.execute(text("DELETE FROM household WHERE id = app_household_id()"))
    finally:
        engine.dispose()
