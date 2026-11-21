"""Alembic: upgrade -> downgrade -> upgrade against a real Postgres."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

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


def _snapshot() -> tuple[tuple[object, ...], ...]:
    import importlib.util

    path = BACKEND / "migrations" / "versions" / "0008_category_tree.py"
    spec = importlib.util.spec_from_file_location("m0008_snapshot", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return tuple(mod.SNAPSHOT)


def test_migration_0008_imports_no_application_code() -> None:
    src = (BACKEND / "migrations" / "versions" / "0008_category_tree.py").read_text()
    assert "from app" not in src and "import app" not in src


def test_live_tree_is_a_superset_of_the_0008_snapshot() -> None:
    """Slugs are never removed, re-parented or re-kinded without a new migration."""
    from app.domain.categories import default_categories

    live = {c.slug: c for c in default_categories()}
    for slug, parent, _name, kind, counts, _order, _essential in _snapshot():
        assert slug in live, f"{slug} was removed from the default tree"
        assert live[slug].parent_slug == parent, slug
        assert live[slug].kind == kind, slug
        assert live[slug].counts_toward_spending == counts, slug


@pytest.mark.services
def test_category_migration_over_data(migrations_database_url: str) -> None:
    """0008 over data: pre-slug categories keep their data and get the spending flag from their
    kind; every household is seeded; down then up neither duplicates nor drops the household's own
    category, and downgrade clears a source stranded by a deleted category."""
    url = migrations_database_url
    cfg = _config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "0007_transaction_live_index")
    engine = create_engine(url)

    def ctx(c: Connection, hid: object) -> None:
        c.execute(
            text(
                "SELECT set_config('app.household_id', :h, true),"
                " set_config('app.household_wide', 'on', true)"
            ),
            {"h": str(hid)},
        )

    try:
        with engine.begin() as c:
            hid = c.execute(
                text("SELECT create_household('Example Migration Household')")
            ).scalar_one()
            ctx(c, hid)
            for name, kind in (("Mine", "expense"), ("Pay", "income"), ("Save", "savings")):
                c.execute(
                    text("INSERT INTO category (household_id, name, kind) VALUES (:h, :n, :k)"),
                    {"h": hid, "n": name, "k": kind},
                )

        def own() -> list[tuple[str, bool]]:
            with engine.begin() as c:
                ctx(c, hid)
                return [
                    (r[0], r[1])
                    for r in c.execute(
                        text(
                            "SELECT name, counts_toward_spending FROM category"
                            " WHERE origin = 'user' ORDER BY name"
                        )
                    )
                ]

        def total() -> tuple[int, int]:
            with engine.begin() as c:
                ctx(c, hid)
                return (
                    c.execute(text("SELECT count(*) FROM category")).scalar_one(),
                    c.execute(
                        text("SELECT count(*) FROM category WHERE origin = 'system'")
                    ).scalar_one(),
                )

        n = len(_snapshot())
        command.upgrade(cfg, "head")
        assert total() == (n + 3, n)
        assert own() == [("Mine", True), ("Pay", False), ("Save", False)]

        # The flag cannot disagree with the kind.
        with engine.begin() as c:
            ctx(c, hid)
            with pytest.raises(IntegrityError), c.begin_nested():
                c.execute(
                    text(
                        "UPDATE category SET counts_toward_spending = true"
                        " WHERE slug = 'income.wages'"
                    )
                )

        # A transaction on a system category, one user-cleared, one on the household's own.
        with engine.begin() as c:
            ctx(c, hid)
            acct = c.execute(
                text(
                    "INSERT INTO account (household_id, source, name, type, visibility, currency)"
                    " VALUES (:h, 'manual', 'Example', 'depository', 'shared', 'USD') RETURNING id"
                ),
                {"h": hid},
            ).scalar_one()
            sys_cat = c.execute(
                text("SELECT id FROM category WHERE slug = 'food.groceries'")
            ).scalar_one()
            own_cat = c.execute(text("SELECT id FROM category WHERE name = 'Mine'")).scalar_one()
            ids = []
            for cat, src in ((sys_cat, "user"), (None, "user"), (own_cat, "user")):
                ids.append(
                    c.execute(
                        text(
                            'INSERT INTO "transaction" (household_id, account_id, posted_date,'
                            " amount, description_raw, source, category_id, category_source)"
                            " VALUES (:h, :a, '2026-01-01', -1.00, 'x', 'manual', :c, :s)"
                            " RETURNING id"
                        ),
                        {"h": hid, "a": acct, "c": cat, "s": src},
                    ).scalar_one()
                )
            budget = c.execute(
                text(
                    "INSERT INTO budget (household_id, name, start_date)"
                    " VALUES (:h, 'B', '2026-01-01') RETURNING id"
                ),
                {"h": hid},
            ).scalar_one()
            line = c.execute(
                text(
                    "INSERT INTO budget_line (household_id, budget_id, category_id, amount)"
                    " VALUES (:h, :b, :c, 10) RETURNING id"
                ),
                {"h": hid, "b": budget, "c": sys_cat},
            ).scalar_one()

        # A budget line on a system category blocks the downgrade, and nothing is changed.
        with pytest.raises(RuntimeError, match="budget line"):
            command.downgrade(cfg, "-1")
        assert total() == (n + 3, n)
        with engine.begin() as c:
            ctx(c, hid)
            c.execute(text("DELETE FROM budget_line WHERE id = :i"), {"i": line})

        command.downgrade(cfg, "-1")
        with engine.begin() as c:
            ctx(c, hid)
            assert c.execute(text("SELECT count(*) FROM category")).scalar_one() == 3
            rows = c.execute(
                text(
                    'SELECT id, category_id, category_source FROM "transaction"'
                    " WHERE id = ANY(:ids)"
                ),
                {"ids": ids},
            ).all()
        by_id = {r[0]: (r[1], r[2]) for r in rows}
        assert by_id[ids[0]] == (None, None)  # its category is gone: no stranded source
        assert by_id[ids[1]] == (None, None)  # a user's "no category" is reset on downgrade
        assert by_id[ids[2]] == (own_cat, "user")  # the household's own category is untouched

        command.upgrade(cfg, "head")
        assert total() == (n + 3, n)
        assert own() == [("Mine", True), ("Pay", False), ("Save", False)]
        with engine.begin() as c:
            ctx(c, hid)
            c.execute(text("DELETE FROM household WHERE id = app_household_id()"))
    finally:
        engine.dispose()
