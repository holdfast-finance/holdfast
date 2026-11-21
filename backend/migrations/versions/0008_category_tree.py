"""Category tree: stable slugs, system vs user origin, default seed (C2).

A category gets a stable machine ``slug`` (unique per household), an ``origin`` (``system`` for the
seeded default tree, ``user`` for the household's own), ``is_hidden``, ``counts_toward_spending``
and ``sort_order``. ``kind`` also allows ``excluded``. Existing rows (if any) keep their data: they
get a ``legacy-<id>`` slug and stay ``user`` origin. Every existing household is then seeded with
the default tree, idempotently on (household_id, slug). RLS is unchanged: ``category`` already has
the household_isolation policy.

``household`` and ``category`` have FORCE RLS, which also binds the owner, so listing every
household and backfilling slugs on every row need it relaxed for part of this transaction. Both are
forced again before the migration ends.

The seed reads the current default tree (``app.domain.categories``); re-running it later only adds
slugs a household lacks.

Revision ID: 0008_category_tree
Revises: 0007_transaction_live_index
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.elements import conv

from app.domain.categories import SEED_SQL, seed_params

revision: str = "0008_category_tree"
down_revision: str | None = "0007_transaction_live_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "kind IN ('income', 'expense', 'transfer', 'savings')"
NEW_KINDS = "kind IN ('income', 'expense', 'transfer', 'savings', 'excluded')"
CHECK = conv("ck_category_kind")
ORIGIN_CHECK = conv("ck_category_origin")
UNIQUE = conv("uq_category_household_id_slug")


def upgrade() -> None:
    op.add_column("category", sa.Column("slug", sa.Text(), nullable=True))
    op.add_column(
        "category",
        sa.Column("origin", sa.Text(), server_default=sa.text("'user'"), nullable=False),
    )
    op.add_column(
        "category",
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "category",
        sa.Column(
            "counts_toward_spending", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "category",
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    # Rows that predate slugs keep their data and get a key that cannot collide with a default.
    # FORCE RLS binds the owner, so without a household context this UPDATE would see no rows.
    op.execute("ALTER TABLE category NO FORCE ROW LEVEL SECURITY")
    op.execute("UPDATE category SET slug = 'legacy-' || id::text WHERE slug IS NULL")
    op.execute("ALTER TABLE category FORCE ROW LEVEL SECURITY")
    op.alter_column("category", "slug", nullable=False)
    op.create_unique_constraint(UNIQUE, "category", ["household_id", "slug"])
    op.create_check_constraint(ORIGIN_CHECK, "category", "origin IN ('system', 'user')")
    op.drop_constraint(CHECK, "category", type_="check")
    op.create_check_constraint(CHECK, "category", NEW_KINDS)

    conn = op.get_bind()
    op.execute("ALTER TABLE household NO FORCE ROW LEVEL SECURITY")
    household_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM household"))]
    op.execute("ALTER TABLE household FORCE ROW LEVEL SECURITY")
    for hid in household_ids:
        conn.execute(sa.text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
        conn.execute(sa.text(SEED_SQL), seed_params(hid))
    conn.execute(sa.text("SELECT set_config('app.household_id', '', true)"))


def downgrade() -> None:
    # Seeded (system) rows are dropped, edits to them included; a household's own categories
    # survive. Foreign keys to a dropped category are SET NULL, so no transaction is lost.
    conn = op.get_bind()
    op.execute("ALTER TABLE household NO FORCE ROW LEVEL SECURITY")
    household_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM household"))]
    op.execute("ALTER TABLE household FORCE ROW LEVEL SECURITY")
    for hid in household_ids:
        conn.execute(sa.text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
        conn.execute(
            sa.text(
                "UPDATE category SET kind = 'expense' WHERE kind = 'excluded' AND origin = 'user'"
            )
        )
        conn.execute(sa.text("DELETE FROM category WHERE origin = 'system'"))
    conn.execute(sa.text("SELECT set_config('app.household_id', '', true)"))
    op.drop_constraint(CHECK, "category", type_="check")
    op.create_check_constraint(CHECK, "category", OLD_KINDS)
    op.drop_constraint(ORIGIN_CHECK, "category", type_="check")
    op.drop_constraint(UNIQUE, "category", type_="unique")
    op.drop_column("category", "sort_order")
    op.drop_column("category", "counts_toward_spending")
    op.drop_column("category", "is_hidden")
    op.drop_column("category", "origin")
    op.drop_column("category", "slug")
