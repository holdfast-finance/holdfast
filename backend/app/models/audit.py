"""Append-only audit log. The app role has INSERT + SELECT only (no UPDATE / DELETE)."""

from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Any

from sqlalchemy import ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """``household_id`` is NULL for pre-auth events (failed logins). Those rows are insert-only:
    RLS hides them from the app role. Never put tokens, keys or SSNs in ``detail``."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_household_id_at", "household_id", "at"),)
    # INSERT ... RETURNING must pass the SELECT policy, which pre-auth rows (household_id NULL)
    # never do. So the id is generated client-side and the ORM never asks for RETURNING.
    __mapper_args__ = {"eager_defaults": False}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str]
    entity: Mapped[str | None]
    entity_id: Mapped[str | None]
    detail: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
