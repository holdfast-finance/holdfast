"""Category seeding (idempotent, never overwrites household edits) and ``GET /api/categories``."""

from __future__ import annotations

import uuid

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session
from app.domain.categories import default_categories
from app.models import Category
from app.openapi_export import openapi_document
from app.services.categories import ensure_default_categories
from tests.api.conftest import Owner, make_client, seed_household

EXPECTED = len(default_categories())


async def _all(s: AsyncSession) -> list[Category]:
    return list((await s.execute(select(Category).order_by(Category.slug))).scalars())


async def test_setup_seeds_the_default_tree(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    async with scoped_session(app.state.session_factory, hid, None) as s:
        rows = await _all(s)
    assert len(rows) == EXPECTED
    assert {r.origin for r in rows} == {"system"}
    by_id = {r.id: r for r in rows}
    for r in rows:
        if r.parent_id is not None:
            assert by_id[r.parent_id].parent_id is None  # two levels


async def test_seeding_is_idempotent_and_keeps_household_edits(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    factory = app.state.session_factory
    async with scoped_session(factory, hid, None) as s:
        groceries = (
            await s.execute(select(Category).where(Category.slug == "food.groceries"))
        ).scalar_one()
        groceries.name = "Supermarket"
        groceries.is_hidden = True
        coffee = (
            await s.execute(select(Category).where(Category.slug == "food.coffee"))
        ).scalar_one()
        coffee.kind = "excluded"
        coffee.counts_toward_spending = False
        s.add(
            Category(
                household_id=hid, slug="custom.hobby", name="Hobby", kind="expense", origin="user"
            )
        )
    for _ in range(2):
        async with scoped_session(factory, hid, None) as s:
            await ensure_default_categories(s, hid)
    async with scoped_session(factory, hid, None) as s:
        rows = {r.slug: r for r in await _all(s)}
        n = (await s.execute(select(func.count()).select_from(Category))).scalar_one()
    assert n == EXPECTED + 1
    assert rows["food.groceries"].name == "Supermarket" and rows["food.groceries"].is_hidden
    assert rows["food.coffee"].kind == "excluded"
    assert rows["custom.hobby"].origin == "user"


async def test_a_missing_default_is_restored_without_touching_the_rest(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    factory = app.state.session_factory
    async with scoped_session(factory, hid, None) as s:
        row = (await s.execute(select(Category).where(Category.slug == "pets.vet"))).scalar_one()
        await s.delete(row)
        renamed = (await s.execute(select(Category).where(Category.slug == "pets"))).scalar_one()
        renamed.name = "Animals"
    async with scoped_session(factory, hid, None) as s:
        await ensure_default_categories(s, hid)
    async with scoped_session(factory, hid, None) as s:
        rows = {r.slug: r for r in await _all(s)}
    assert len(rows) == EXPECTED
    assert rows["pets"].name == "Animals"
    assert rows["pets.vet"].parent_id == rows["pets"].id


async def test_requires_a_session(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/categories")
    assert r.status_code == 401


async def test_api_returns_the_tree_and_isolates_households(app: FastAPI, owner: Owner) -> None:
    r = await owner.client.get("/api/categories")
    assert r.status_code == 200
    items = r.json()["items"]
    groups = {g["slug"]: g for g in items}
    assert {"income", "housing", "uncategorized"} <= set(groups)
    assert sum(1 + len(g["children"]) for g in items) == EXPECTED
    groc = next(c for c in groups["food"]["children"] if c["slug"] == "food.groceries")
    assert groc["kind"] == "expense" and groc["origin"] == "system"
    assert groc["is_essential"] is True and groc["counts_toward_spending"] is True
    assert groc["children"] == []
    assert groups["transfers"]["kind"] == "transfer"
    ids_a = {g["id"] for g in items} | {c["id"] for g in items for c in g["children"]}

    hid_b, _, token = await seed_household(app, "Other Example Household", "Bo", "bo@example.com")
    async with make_client(app) as b:
        b.cookies.set("hf_session", token)
        # Nothing is seeded for a household made without the setup path: it sees only its own.
        assert (await b.get("/api/categories")).json() == {"items": []}
    async with scoped_session(app.state.session_factory, hid_b, None) as s:
        await ensure_default_categories(s, hid_b)
    async with make_client(app) as b:
        b.cookies.set("hf_session", token)
        body = (await b.get("/api/categories")).json()["items"]
        ids_b = {g["id"] for g in body} | {c["id"] for g in body for c in g["children"]}
        assert ids_b and not ids_a & ids_b


async def test_a_household_cannot_seed_into_another(app: FastAPI, owner: Owner) -> None:
    import pytest
    from sqlalchemy.exc import DBAPIError

    hid_a = uuid.UUID(owner.household["id"])
    hid_b, _, _ = await seed_household(app, "Other Example Household", "Bo", "bo@example.com")
    async with scoped_session(app.state.session_factory, hid_b, None) as s:
        with pytest.raises(DBAPIError):
            await ensure_default_categories(s, hid_a)


def test_openapi_documents_the_route() -> None:
    doc = openapi_document()
    assert "get" in doc["paths"]["/api/categories"]
