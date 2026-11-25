"""Account ownership request (A1). Response fields live on ``AccountOut``."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from app.schemas.common import Model

OwnerKind = Literal["me", "joint", "other"]
AccountVisibilityIn = Literal["private", "shared", "summary_only"]


class AccountOwnershipIn(Model):
    """Change who owns an account, or how visible it is. At least one field is set."""

    owner: Literal["me", "joint"] | None = None
    visibility: AccountVisibilityIn | None = None

    @model_validator(mode="after")
    def at_least_one(self) -> AccountOwnershipIn:
        if self.owner is None and self.visibility is None:
            raise ValueError("Say what to change.")
        return self
