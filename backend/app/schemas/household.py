"""Household, people and audit-log schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import model_validator

from app.schemas.common import FilingRole, Model, Name


class HouseholdUpdate(Model):
    name: Name


class PersonOut(Model):
    id: uuid.UUID
    display_name: str
    filing_role: FilingRole
    has_account: bool


class PeopleList(Model):
    items: list[PersonOut]


class PersonCreate(Model):
    display_name: Name
    filing_role: FilingRole


class PersonUpdate(Model):
    display_name: Name | None = None
    filing_role: FilingRole | None = None

    @model_validator(mode="after")
    def _something(self) -> PersonUpdate:
        if self.display_name is None and self.filing_role is None:
            raise ValueError("Nothing to change.")
        return self


class AuditEntry(Model):
    id: uuid.UUID
    created_at: datetime
    action: str
    actor_display_name: str | None
    ip: str | None
    outcome: Literal["success", "failure"]


class AuditPage(Model):
    items: list[AuditEntry]
    next_before: str | None
