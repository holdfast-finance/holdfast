"""``/api/categories``: the household's category tree (read only)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Ctx, CurrentPrincipal
from app.schemas.categories import CategoryTree
from app.services.categories import CategoryService

router = APIRouter(tags=["categories"])


@router.get("/categories")
async def list_categories(ctx: Ctx, principal: CurrentPrincipal) -> CategoryTree:
    return await CategoryService(ctx, principal).tree()
