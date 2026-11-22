"""The default category tree and the Plaid PFC mapping, loaded from reviewed data files.

Pure: no database. ``app.services.categories`` seeds households from ``default_categories()`` and
``pfc_category_slug`` is the one lookup from a Plaid Personal Finance Category to a slug
(ADR 0022). A slug is ``<group>.<category>`` for a category and ``<group>`` for a group.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

DATA = Path(__file__).resolve().parent
TREE_FILE = DATA / "category_tree.yaml"
PFC_MAP_FILE = DATA / "plaid_pfc_map.yaml"
KINDS = ("income", "expense", "transfer", "savings", "excluded")


@dataclass(frozen=True)
class DefaultCategory:
    slug: str
    parent_slug: str | None
    name: str
    kind: str
    is_essential: bool
    counts_toward_spending: bool
    sort_order: int


def _bool(value: Any, default: bool, where: str) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{where}: expected true or false")
    return value


@cache
def default_categories() -> tuple[DefaultCategory, ...]:
    """Groups and categories in display order; every group precedes its categories."""
    doc = yaml.safe_load(TREE_FILE.read_text(encoding="utf-8"))
    out: list[DefaultCategory] = []
    seen: set[str] = set()

    def add(c: DefaultCategory) -> None:
        if c.kind not in KINDS:
            raise ValueError(f"{c.slug}: unknown kind {c.kind!r}")
        if c.slug in seen:
            raise ValueError(f"duplicate slug {c.slug}")
        seen.add(c.slug)
        out.append(c)

    for gi, group in enumerate(doc["groups"]):
        g_slug, g_kind = group["slug"], group["kind"]
        g_essential = _bool(group.get("essential"), False, g_slug)
        add(
            DefaultCategory(
                g_slug,
                None,
                group["name"],
                g_kind,
                g_essential,
                _bool(group.get("counts_toward_spending"), g_kind == "expense", g_slug),
                gi * 1000,
            )
        )
        for ci, cat in enumerate(group["categories"], start=1):
            slug = f"{g_slug}.{cat['slug']}"
            kind = cat.get("kind", g_kind)
            add(
                DefaultCategory(
                    slug,
                    g_slug,
                    cat["name"],
                    kind,
                    _bool(cat.get("essential"), g_essential, slug),
                    _bool(cat.get("counts_toward_spending"), kind == "expense", slug),
                    gi * 1000 + ci,
                )
            )
    return tuple(out)


def default_slugs() -> frozenset[str]:
    return frozenset(c.slug for c in default_categories())


@dataclass(frozen=True)
class PfcMapping:
    primary: dict[str, str]
    detailed: dict[str, str]
    unmapped: dict[str, str]  # code (primary or detailed) -> reason


@cache
def pfc_mapping() -> PfcMapping:
    doc = yaml.safe_load(PFC_MAP_FILE.read_text(encoding="utf-8"))
    return PfcMapping(
        primary=dict(doc["primary"]),
        detailed=dict(doc["detailed"]),
        unmapped=dict(doc["unmapped"]),
    )


TAXONOMY_FILE = DATA / "plaid_pfc_taxonomy.yaml"


def pfc_taxonomy_meta() -> dict[str, str]:
    doc = yaml.safe_load(TAXONOMY_FILE.read_text(encoding="utf-8"))
    return {k: str(doc[k]) for k in ("source_url", "retrieved", "upstream_csv_sha256")}


def pfc_taxonomy() -> list[tuple[str, str]]:
    """``(primary, detailed)`` pairs from the official taxonomy file."""
    doc = yaml.safe_load(TAXONOMY_FILE.read_text(encoding="utf-8"))
    return [(p, d) for p, ds in doc["codes"].items() for d in ds]


def pfc_category_slug(primary: str | None, detailed: str | None) -> str | None:
    """The default category slug for a Plaid PFC, or ``None`` when it is left for the user.

    A mapped detailed code wins. An explicitly unmapped detailed code stays unmapped. A detailed
    code Plaid added after our taxonomy falls back to its primary code's slug.
    """
    m = pfc_mapping()
    if detailed:
        if detailed in m.detailed:
            return m.detailed[detailed]
        if detailed in m.unmapped:
            return None
    if primary:
        return m.primary.get(primary)
    return None


# One statement seeds one row and is safe to repeat: the unique (household_id, slug) key means an
# existing row, renamed, hidden or otherwise edited by the household, is left exactly as it is.
# Groups come first so a child finds its parent. Used by the migration (sync) and the app (async).
SEED_SQL = (
    "INSERT INTO category (household_id, parent_id, slug, name, kind, origin,"
    " counts_toward_spending, sort_order, is_essential)"
    " VALUES (:household_id,"
    " (SELECT id FROM category WHERE household_id = :household_id AND slug = :parent_slug),"
    " :slug, :name, :kind, 'system', :counts, :sort_order, :essential)"
    " ON CONFLICT (household_id, slug) DO NOTHING"
)


def seed_params(household_id: object) -> list[dict[str, object]]:
    return [
        {
            "household_id": household_id,
            "parent_slug": c.parent_slug,
            "slug": c.slug,
            "name": c.name,
            "kind": c.kind,
            "counts": c.counts_toward_spending,
            "sort_order": c.sort_order,
            "essential": c.is_essential,
        }
        for c in default_categories()
    ]
