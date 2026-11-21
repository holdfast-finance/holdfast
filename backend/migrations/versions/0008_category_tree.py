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

The default tree and the seed SQL below are frozen literals: this migration imports no application
code, so later edits to ``category_tree.yaml`` cannot change what 0008 does. A test asserts the
live tree stays a superset of this snapshot (``tests/domain/test_category_tree.py``).

``counts_toward_spending`` is tied to ``kind`` by a CHECK (true only for ``expense``): there is no
legitimate reason for an expense to be uncounted (hide it, or use kind ``excluded``), and a flag
that can disagree with the kind makes metrics wrong. Pre-existing user categories are backfilled
to ``kind = 'expense'``.

Downgrade: system categories are deleted. Transactions and splits assigned to one lose the
assignment (``category_id`` goes NULL through the FK) and, on transactions, ``category_source`` is
cleared where no category is left, which also resets a user's explicit "no category" choice.
``budget_line.category_id`` is ON DELETE CASCADE, so the downgrade refuses to run while any budget
line points at a system category rather than deleting it silently.

Revision ID: 0008_category_tree
Revises: 0007_transaction_live_index
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.elements import conv

revision: str = "0008_category_tree"
down_revision: str | None = "0007_transaction_live_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "kind IN ('income', 'expense', 'transfer', 'savings')"
NEW_KINDS = "kind IN ('income', 'expense', 'transfer', 'savings', 'excluded')"
CHECK = conv("ck_category_kind")
ORIGIN_CHECK = conv("ck_category_origin")
UNIQUE = conv("uq_category_household_id_slug")
SPENDING_CHECK = conv("ck_category_counts_spending_only_expense")

# (slug, parent_slug, name, kind, counts_toward_spending, sort_order, is_essential), groups first.
SNAPSHOT: tuple[tuple[str, str | None, str, str, bool, int, bool], ...] = (
    ("income", None, "Income", "income", False, 0, False),
    ("income.wages", "income", "Wages and salary", "income", False, 1, False),
    ("income.interest", "income", "Interest earned", "income", False, 2, False),
    ("income.dividends", "income", "Dividends", "income", False, 3, False),
    ("income.pension", "income", "Pension and retirement income", "income", False, 4, False),
    ("income.benefits", "income", "Benefits and unemployment", "income", False, 5, False),
    ("income.tax-refund", "income", "Tax refund", "income", False, 6, False),
    ("income.other", "income", "Other income", "income", False, 7, False),
    ("transfers", None, "Transfers", "transfer", False, 1000, False),
    ("transfers.account", "transfers", "Account transfer", "transfer", False, 1001, False),
    (
        "transfers.credit-card-payment",
        "transfers",
        "Credit card payment",
        "transfer",
        False,
        1002,
        False,
    ),
    ("transfers.cash-withdrawal", "transfers", "Cash withdrawal", "transfer", False, 1003, False),
    ("transfers.deposit", "transfers", "Deposit", "transfer", False, 1004, False),
    (
        "transfers.loan-proceeds",
        "transfers",
        "Loan and cash advance proceeds",
        "transfer",
        False,
        1005,
        False,
    ),
    ("transfers.other", "transfers", "Other transfer", "transfer", False, 1006, False),
    ("transfers.ignored", "transfers", "Ignored", "excluded", False, 1007, False),
    ("housing", None, "Housing", "expense", True, 2000, True),
    ("housing.rent", "housing", "Rent", "expense", True, 2001, True),
    ("housing.mortgage", "housing", "Mortgage", "expense", True, 2002, True),
    ("housing.maintenance", "housing", "Repairs and maintenance", "expense", True, 2003, True),
    ("housing.furnishings", "housing", "Furniture and furnishings", "expense", True, 2004, False),
    ("housing.home-supplies", "housing", "Hardware and home supplies", "expense", True, 2005, True),
    ("housing.security", "housing", "Home security", "expense", True, 2006, True),
    ("housing.other", "housing", "Other housing", "expense", True, 2007, True),
    ("utilities", None, "Utilities", "expense", True, 3000, True),
    ("utilities.electricity-gas", "utilities", "Electricity and gas", "expense", True, 3001, True),
    ("utilities.water", "utilities", "Water", "expense", True, 3002, True),
    ("utilities.waste", "utilities", "Sewage and waste", "expense", True, 3003, True),
    ("utilities.internet-cable", "utilities", "Internet and cable", "expense", True, 3004, False),
    ("utilities.phone", "utilities", "Phone", "expense", True, 3005, True),
    ("utilities.other", "utilities", "Other utilities", "expense", True, 3006, True),
    ("food", None, "Food and drink", "expense", True, 4000, False),
    ("food.groceries", "food", "Groceries", "expense", True, 4001, True),
    ("food.restaurants", "food", "Restaurants", "expense", True, 4002, False),
    ("food.fast-food", "food", "Fast food", "expense", True, 4003, False),
    ("food.coffee", "food", "Coffee", "expense", True, 4004, False),
    ("food.alcohol", "food", "Beer", "expense", True, 4005, False),
    ("food.other", "food", "Other food and drink", "expense", True, 4006, False),
    ("transport", None, "Transportation", "expense", True, 5000, False),
    ("transport.fuel", "transport", "Fuel", "expense", True, 5001, True),
    ("transport.public-transit", "transport", "Public transit", "expense", True, 5002, True),
    ("transport.parking", "transport", "Parking", "expense", True, 5003, False),
    ("transport.tolls", "transport", "Tolls", "expense", True, 5004, False),
    ("transport.rideshare", "transport", "Taxis and ride shares", "expense", True, 5005, False),
    ("transport.auto-service", "transport", "Auto service and repair", "expense", True, 5006, True),
    ("transport.bikes-scooters", "transport", "Bikes and scooters", "expense", True, 5007, False),
    ("transport.other", "transport", "Other transportation", "expense", True, 5008, False),
    ("health", None, "Health and personal care", "expense", True, 6000, False),
    ("health.medical", "health", "Doctor and primary care", "expense", True, 6001, True),
    ("health.dental", "health", "Dental", "expense", True, 6002, True),
    ("health.vision", "health", "Eye care", "expense", True, 6003, True),
    ("health.nursing", "health", "Nursing and home care", "expense", True, 6004, True),
    ("health.pharmacy", "health", "Pharmacy and supplements", "expense", True, 6005, True),
    ("health.fitness", "health", "Gym and fitness", "expense", True, 6006, False),
    ("health.personal-care", "health", "Hair", "expense", True, 6007, False),
    ("health.other", "health", "Other health", "expense", True, 6008, True),
    ("insurance", None, "Insurance", "expense", True, 7000, True),
    ("insurance.health", "insurance", "Health insurance", "expense", True, 7001, True),
    ("insurance.auto", "insurance", "Auto insurance", "expense", True, 7002, True),
    ("insurance.home", "insurance", "Home and renters insurance", "expense", True, 7003, True),
    ("insurance.life", "insurance", "Life and disability insurance", "expense", True, 7004, True),
    ("insurance.other", "insurance", "Other insurance", "expense", True, 7005, True),
    ("debt", None, "Debt payments", "expense", True, 8000, True),
    ("debt.car-loan", "debt", "Car loan", "expense", True, 8001, True),
    ("debt.student-loan", "debt", "Student loan", "expense", True, 8002, True),
    ("debt.personal-loan", "debt", "Personal loan", "expense", True, 8003, True),
    ("debt.other", "debt", "Other loan payment", "expense", True, 8004, True),
    ("savings", None, "Savings and investing", "savings", False, 9000, False),
    ("savings.savings", "savings", "Savings contribution", "savings", False, 9001, False),
    ("savings.investing", "savings", "Investment contribution", "savings", False, 9002, False),
    ("savings.retirement", "savings", "Retirement contribution", "savings", False, 9003, False),
    ("shopping", None, "Shopping", "expense", True, 10000, False),
    ("shopping.general", "shopping", "General merchandise", "expense", True, 10001, False),
    ("shopping.clothing", "shopping", "Clothing and accessories", "expense", True, 10002, False),
    ("shopping.electronics", "shopping", "Electronics", "expense", True, 10003, False),
    ("shopping.books", "shopping", "Books and media", "expense", True, 10004, False),
    ("shopping.sporting-goods", "shopping", "Sporting goods", "expense", True, 10005, False),
    ("shopping.office-supplies", "shopping", "Office supplies", "expense", True, 10006, False),
    ("shopping.other", "shopping", "Other shopping", "expense", True, 10007, False),
    ("entertainment", None, "Entertainment", "expense", True, 11000, False),
    ("entertainment.streaming", "entertainment", "TV", "expense", True, 11001, False),
    ("entertainment.music", "entertainment", "Music and audio", "expense", True, 11002, False),
    ("entertainment.games", "entertainment", "Video games", "expense", True, 11003, False),
    ("entertainment.events", "entertainment", "Events", "expense", True, 11004, False),
    (
        "entertainment.gambling",
        "entertainment",
        "Casinos and gambling",
        "expense",
        True,
        11005,
        False,
    ),
    ("entertainment.other", "entertainment", "Other entertainment", "expense", True, 11006, False),
    ("travel", None, "Travel", "expense", True, 12000, False),
    ("travel.flights", "travel", "Flights", "expense", True, 12001, False),
    ("travel.lodging", "travel", "Lodging", "expense", True, 12002, False),
    ("travel.rental-cars", "travel", "Rental cars", "expense", True, 12003, False),
    ("travel.other", "travel", "Other travel", "expense", True, 12004, False),
    ("education", None, "Education", "expense", True, 13000, False),
    ("education.tuition", "education", "Tuition and courses", "expense", True, 13001, False),
    ("education.supplies", "education", "Books and school supplies", "expense", True, 13002, False),
    ("education.other", "education", "Other education", "expense", True, 13003, False),
    ("family", None, "Kids and family", "expense", True, 14000, False),
    ("family.childcare", "family", "Childcare", "expense", True, 14001, True),
    (
        "family.kids-supplies",
        "family",
        "Kids' clothing and supplies",
        "expense",
        True,
        14002,
        False,
    ),
    ("family.kids-activities", "family", "Kids' activities", "expense", True, 14003, False),
    ("family.other", "family", "Other family", "expense", True, 14004, False),
    ("pets", None, "Pets", "expense", True, 15000, False),
    ("pets.food-supplies", "pets", "Pet food and supplies", "expense", True, 15001, False),
    ("pets.vet", "pets", "Veterinary", "expense", True, 15002, False),
    ("pets.other", "pets", "Other pet costs", "expense", True, 15003, False),
    ("gifts", None, "Gifts and charity", "expense", True, 16000, False),
    ("gifts.gifts", "gifts", "Gifts", "expense", True, 16001, False),
    ("gifts.charity", "gifts", "Donations and charity", "expense", True, 16002, False),
    ("taxes", None, "Taxes", "expense", True, 17000, True),
    ("taxes.income-tax", "taxes", "Income tax payments", "expense", True, 17001, True),
    ("taxes.property-tax", "taxes", "Property tax", "expense", True, 17002, True),
    (
        "taxes.government-fees",
        "taxes",
        "Government fees and services",
        "expense",
        True,
        17003,
        True,
    ),
    ("taxes.other", "taxes", "Other taxes", "expense", True, 17004, True),
    ("fees", None, "Fees, interest and services", "expense", True, 18000, False),
    ("fees.bank-fees", "fees", "Bank fees", "expense", True, 18001, False),
    ("fees.interest-charges", "fees", "Interest charges", "expense", True, 18002, False),
    ("fees.professional-services", "fees", "Accounting", "expense", True, 18003, False),
    ("fees.postage-shipping", "fees", "Postage and shipping", "expense", True, 18004, False),
    ("fees.storage", "fees", "Storage", "expense", True, 18005, False),
    ("fees.other-services", "fees", "Other services", "expense", True, 18006, False),
    ("business", None, "Business", "expense", True, 19000, False),
    ("business.supplies", "business", "Business supplies", "expense", True, 19001, False),
    ("business.software", "business", "Software and subscriptions", "expense", True, 19002, False),
    ("business.travel", "business", "Business travel", "expense", True, 19003, False),
    (
        "business.professional-services",
        "business",
        "Business professional services",
        "expense",
        True,
        19004,
        False,
    ),
    ("business.other", "business", "Other business", "expense", True, 19005, False),
    ("uncategorized", None, "Uncategorized", "expense", True, 20000, False),
    (
        "uncategorized.needs-review",
        "uncategorized",
        "Needs a category",
        "expense",
        True,
        20001,
        False,
    ),
)


SEED_SQL = (
    "INSERT INTO category (household_id, parent_id, slug, name, kind, origin,"
    " counts_toward_spending, sort_order, is_essential)"
    " VALUES (:household_id,"
    " (SELECT id FROM category WHERE household_id = :household_id AND slug = :parent_slug),"
    " :slug, :name, :kind, 'system', :counts, :sort_order, :essential)"
    " ON CONFLICT (household_id, slug) DO NOTHING"
)


def _seed_params(household_id: object) -> list[dict[str, object]]:
    return [
        {
            "household_id": household_id,
            "parent_slug": parent,
            "slug": slug,
            "name": name,
            "kind": kind,
            "counts": counts,
            "sort_order": order,
            "essential": essential,
        }
        for slug, parent, name, kind, counts, order, essential in SNAPSHOT
    ]


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
    # Existing rows are the household's own: spending when (and only when) they are expenses.
    op.execute("UPDATE category SET counts_toward_spending = (kind = 'expense')")
    op.execute("ALTER TABLE category FORCE ROW LEVEL SECURITY")
    op.alter_column("category", "slug", nullable=False)
    op.create_check_constraint(
        SPENDING_CHECK, "category", "counts_toward_spending = false OR kind = 'expense'"
    )
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
        conn.execute(sa.text(SEED_SQL), _seed_params(hid))
    conn.execute(sa.text("SELECT set_config('app.household_id', '', true)"))


def downgrade() -> None:
    # System categories are dropped, edits to them included; a household's own categories survive.
    # Foreign keys to a dropped category are SET NULL (transactions, splits, rules) so no row is
    # lost, but the assignment is. budget_line is ON DELETE CASCADE, so refuse rather than let it
    # delete a budget line silently.
    conn = op.get_bind()
    op.execute("ALTER TABLE household NO FORCE ROW LEVEL SECURITY")
    household_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM household"))]
    op.execute("ALTER TABLE household FORCE ROW LEVEL SECURITY")
    for hid in household_ids:
        conn.execute(sa.text("SELECT set_config('app.household_id', :h, true)"), {"h": str(hid)})
        lines = conn.execute(
            sa.text(
                "SELECT count(*) FROM budget_line b JOIN category c"
                " ON c.id = b.category_id AND c.household_id = b.household_id"
                " WHERE c.origin = 'system'"
            )
        ).scalar_one()
        if lines:
            raise RuntimeError(
                f"cannot downgrade 0008: {lines} budget line(s) use a default category; "
                "move them to a category you created, or delete them, then retry"
            )
        conn.execute(
            sa.text(
                "UPDATE category SET kind = 'expense' WHERE kind = 'excluded' AND origin = 'user'"
            )
        )
        conn.execute(sa.text("DELETE FROM category WHERE origin = 'system'"))
        # The one place the source is cleared without a user action: a source with no category
        # left would otherwise strand 'user' on a row whose choice no longer exists.
        conn.execute(
            sa.text(
                'UPDATE "transaction" SET category_source = NULL'
                " WHERE category_id IS NULL AND category_source IS NOT NULL"
            )
        )
    conn.execute(sa.text("SELECT set_config('app.household_id', '', true)"))
    op.drop_constraint(SPENDING_CHECK, "category", type_="check")
    op.drop_constraint(CHECK, "category", type_="check")
    op.create_check_constraint(CHECK, "category", OLD_KINDS)
    op.drop_constraint(ORIGIN_CHECK, "category", type_="check")
    op.drop_constraint(UNIQUE, "category", type_="unique")
    op.drop_column("category", "sort_order")
    op.drop_column("category", "counts_toward_spending")
    op.drop_column("category", "is_hidden")
    op.drop_column("category", "origin")
    op.drop_column("category", "slug")
