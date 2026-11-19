"""AI advisor conversations, messages and findings (agent writes go only to findings)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import SmallInteger, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    CONVERSATION_KINDS,
    FINDING_SEVERITIES,
    FINDING_STATUSES,
    MESSAGE_ROLES,
    UUIDPK,
    CreatedAt,
    HouseholdId,
    Money,
    check_in,
    child_fk,
    parent_key,
)


class AgentConversation(Base):
    __tablename__ = "agent_conversation"
    __table_args__ = (check_in("kind", CONVERSATION_KINDS), parent_key())

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    kind: Mapped[str] = mapped_column(server_default=text("'chat'"))
    title: Mapped[str | None]
    created_at: Mapped[CreatedAt]


class AgentMessage(Base):
    __tablename__ = "agent_message"
    __table_args__ = (
        check_in("role", MESSAGE_ROLES),
        child_fk("conversation_id", "agent_conversation"),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    conversation_id: Mapped[uuid.UUID] = mapped_column(index=True)
    role: Mapped[str]
    content: Mapped[dict[str, Any]]
    tool_calls: Mapped[list[Any] | None]
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    created_at: Mapped[CreatedAt]


class AgentFinding(Base):
    __tablename__ = "agent_finding"
    __table_args__ = (
        check_in("severity", FINDING_SEVERITIES),
        check_in("status", FINDING_STATUSES),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    tax_year: Mapped[int | None] = mapped_column(SmallInteger)
    severity: Mapped[str] = mapped_column(server_default=text("'info'"))
    category: Mapped[str]
    title: Mapped[str]
    body_md: Mapped[str]
    est_savings: Mapped[Money | None]
    status: Mapped[str] = mapped_column(server_default=text("'open'"))
    source_refs: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
