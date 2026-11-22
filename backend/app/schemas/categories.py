"""Category tree read schema. Two levels: group -> category."""

from __future__ import annotations

import uuid
from typing import Literal

from app.schemas.common import Model

CategoryKind = Literal["income", "expense", "transfer", "savings", "excluded"]
CategoryOrigin = Literal["system", "user"]


class CategoryNode(Model):
    id: uuid.UUID
    slug: str  # stable machine key; never changes when the household renames the category
    name: str
    kind: CategoryKind
    origin: CategoryOrigin
    is_hidden: bool
    is_essential: bool
    counts_toward_spending: bool
    children: list[CategoryNode]


class CategoryTree(Model):
    items: list[CategoryNode]
