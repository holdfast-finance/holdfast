"""Household, people and per-household encrypted settings."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import FILING_ROLES, UUIDPK, CreatedAt, HouseholdId, check_in, parent_key


class Household(Base):
    """The tenant. RLS: ``id = app_household_id()``. Created only via ``create_household()``."""

    __tablename__ = "household"

    id: Mapped[UUIDPK]
    name: Mapped[str]
    created_at: Mapped[CreatedAt]


class Person(Base):
    __tablename__ = "person"
    __table_args__ = (check_in("filing_role", FILING_ROLES), parent_key())

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    display_name: Mapped[str]
    filing_role: Mapped[str]
    created_at: Mapped[CreatedAt]


class HouseholdSetting(Base):
    """Bring-your-own-keys settings (PLAN 17.4). ``value_enc`` is Fernet ciphertext."""

    __tablename__ = "household_setting"

    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("household.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(primary_key=True)
    value_enc: Mapped[bytes]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
