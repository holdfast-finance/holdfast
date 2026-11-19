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


def test_single_head_with_baseline() -> None:
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert script.get_heads() == ["0001_baseline"]


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
