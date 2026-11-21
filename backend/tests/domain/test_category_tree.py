"""The default tree and the Plaid PFC mapping are consistent with each other and the taxonomy."""

from __future__ import annotations

import re

from app.domain.categories import (
    KINDS,
    PFC_MAP_FILE,
    TAXONOMY_FILE,
    default_categories,
    default_slugs,
    pfc_category_slug,
    pfc_mapping,
    pfc_taxonomy,
    pfc_taxonomy_meta,
)

SLUG = re.compile(r"^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)?$")
REQUIRED_GROUPS = {
    "income", "transfers", "housing", "utilities", "food", "transport", "health", "insurance",
    "debt", "savings", "shopping", "entertainment", "travel", "education", "family", "pets",
    "gifts", "taxes", "fees", "business", "uncategorized",
}  # fmt: skip


def test_tree_is_two_levels_with_unique_valid_slugs() -> None:
    cats = default_categories()
    slugs = [c.slug for c in cats]
    assert len(slugs) == len(set(slugs))
    assert all(SLUG.match(s) for s in slugs)
    groups = {c.slug for c in cats if c.parent_slug is None}
    assert groups >= REQUIRED_GROUPS
    for c in cats:
        assert c.kind in KINDS
        if c.parent_slug is not None:
            assert c.parent_slug in groups  # no third level
            assert c.slug.startswith(c.parent_slug + ".")
    assert all(any(c.parent_slug == g for c in cats) for g in groups)


def test_groups_precede_their_categories_and_sort_order_is_total() -> None:
    cats = default_categories()
    seen: set[str] = set()
    for c in cats:
        if c.parent_slug is not None:
            assert c.parent_slug in seen
        seen.add(c.slug)
    orders = [c.sort_order for c in cats]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)


def test_spending_flag_follows_kind() -> None:
    for c in default_categories():
        assert c.counts_toward_spending == (c.kind == "expense"), c.slug


def test_every_taxonomy_code_is_mapped_or_explicitly_unmapped() -> None:
    m = pfc_mapping()
    pairs = pfc_taxonomy()
    assert len(pairs) > 90  # the real file, not a stub
    primaries = {p for p, _ in pairs}
    detailed = [d for _, d in pairs]
    assert len(detailed) == len(set(detailed))
    for p in primaries:
        assert (p in m.primary) != (p in m.unmapped), f"primary {p}: need exactly one entry"
    for d in detailed:
        assert (d in m.detailed) != (d in m.unmapped), f"detailed {d}: need exactly one entry"


def test_mapping_has_no_stray_codes_and_targets_existing_slugs() -> None:
    m = pfc_mapping()
    pairs = pfc_taxonomy()
    primaries = {p for p, _ in pairs}
    detailed = {d for _, d in pairs}
    assert set(m.primary) <= primaries
    assert set(m.detailed) <= detailed
    assert set(m.unmapped) <= primaries | detailed
    slugs = default_slugs()
    for code, slug in {**m.primary, **m.detailed}.items():
        assert slug in slugs, f"{code} -> unknown slug {slug}"
    assert all(reason.strip() for reason in m.unmapped.values())


def test_every_detailed_code_belongs_to_its_primary() -> None:
    for p, d in pfc_taxonomy():
        assert d.startswith(p + "_"), (p, d)


def test_taxonomy_file_is_sourced() -> None:
    meta = pfc_taxonomy_meta()
    assert meta["source_url"].startswith("https://plaid.com/")
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", meta["retrieved"])
    assert re.match(r"^[0-9a-f]{64}$", meta["upstream_csv_sha256"])
    for f in (PFC_MAP_FILE, TAXONOMY_FILE):
        assert "TODO_VERIFY" not in f.read_text(encoding="utf-8")


def test_lookup_prefers_detailed_then_primary_and_leaves_unmapped_alone() -> None:
    assert pfc_category_slug("FOOD_AND_DRINK", "FOOD_AND_DRINK_GROCERIES") == "food.groceries"
    assert pfc_category_slug("FOOD_AND_DRINK", "FOOD_AND_DRINK_NEW_CODE") == "food.other"
    assert pfc_category_slug("FOOD_AND_DRINK", None) == "food.other"
    assert (
        pfc_category_slug("GOVERNMENT_AND_NON_PROFIT", "GOVERNMENT_AND_NON_PROFIT_TAX_PAYMENT")
        == "taxes.other"
    )
    assert pfc_category_slug("GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_SUPERSTORES") is None
    assert pfc_category_slug("GENERAL_MERCHANDISE", "GENERAL_MERCHANDISE_NEW") is None
    assert pfc_category_slug(None, None) is None
    assert pfc_category_slug("NOT_A_PRIMARY", "NOT_A_CODE") is None


def test_coverage_numbers() -> None:
    m = pfc_mapping()
    pairs = pfc_taxonomy()
    total = len({p for p, _ in pairs}) + len({d for _, d in pairs})
    assert len(m.primary) + len(m.detailed) + len(m.unmapped) == total
