"""Seeding and reading the household's category tree (ADR 0022).

``ensure_default_categories`` is idempotent and never touches an existing row, so it is safe at
household creation and again later (for example when a release adds a default category).
Reads go through ``CategoryService`` under the caller's RLS context. There are no write endpoints
yet; renaming, hiding and adding categories arrive with the categorisation UI.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session
from app.domain.categories import SEED_SQL, seed_params
from app.models import Category
from app.schemas.categories import CategoryNode, CategoryTree
from app.services.context import RequestContext
from app.services.sessions import Principal


async def ensure_default_categories(session: AsyncSession, household_id: uuid.UUID) -> None:
    """Add any default category the household lacks.

    The session must already run in that household's RLS context (``scoped_session``, or
    ``apply_context`` right after ``create_household``); RLS rejects the insert otherwise.
    """
    await session.execute(text(SEED_SQL), seed_params(household_id))


class CategoryService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    def _session(self) -> AbstractAsyncContextManager[AsyncSession]:
        return scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        )

    async def tree(self) -> CategoryTree:
        stmt = select(Category).order_by(Category.sort_order, Category.name, Category.id)
        async with self._session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        nodes = {
            r.id: CategoryNode.model_validate(
                {
                    "id": r.id,
                    "slug": r.slug,
                    "name": r.name,
                    "kind": r.kind,
                    "origin": r.origin,
                    "is_hidden": r.is_hidden,
                    "is_essential": r.is_essential,
                    "counts_toward_spending": r.counts_toward_spending,
                    "children": [],
                }
            )
            for r in rows
        }
        roots: list[CategoryNode] = []
        for r in rows:
            parent = nodes.get(r.parent_id) if r.parent_id else None
            (parent.children if parent else roots).append(nodes[r.id])
        return CategoryTree(items=roots)
