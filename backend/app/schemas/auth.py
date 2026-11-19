"""Auth, setup and invite schemas (Phase 1 contract, as the web client expects them).

User JSON never carries a password hash, TOTP secret or credential public key.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas.common import Code, Email, FilingRole, Model, Name, Token, UserRole
from app.security.passwords import MAX_LENGTH, MIN_LENGTH


class UserPersonOut(Model):
    id: uuid.UUID
    display_name: str
    filing_role: FilingRole


class UserOut(Model):
    id: uuid.UUID
    email: str
    role: UserRole
    person: UserPersonOut | None
    totp_enabled: bool
    has_password: bool


class HouseholdOut(Model):
    id: uuid.UUID
    name: str


class SessionOut(Model):
    user: UserOut
    household: HouseholdOut


class MeOut(SessionOut):
    reauth_fresh: bool


class UserResponse(Model):
    user: UserOut


class SetupStatusOut(Model):
    needs_setup: bool


class SetupIn(Model):
    setup_token: Token
    household_name: Name
    display_name: Name
    email: Email


class PasskeyRegisterOptionsIn(Model):
    nickname: str | None = Field(default=None, max_length=100)


class PasskeyRegisterVerifyIn(Model):
    credential: dict[str, Any]
    nickname: str | None = Field(default=None, max_length=100)


class PasskeyAssertionIn(Model):
    credential: dict[str, Any]


class CredentialOut(Model):
    id: uuid.UUID
    nickname: str
    created_at: datetime


class PasskeyRegisterVerifyOut(Model):
    credential: CredentialOut


class PasskeyOut(CredentialOut):
    last_used_at: datetime | None


class PasskeyList(Model):
    items: list[PasskeyOut]


class PasswordLoginIn(Model):
    email: str = Field(max_length=512)
    password: str = Field(max_length=MAX_LENGTH)
    totp_code: Code | None = None
    recovery_code: Code | None = None

    @model_validator(mode="after")
    def _one_second_factor(self) -> PasswordLoginIn:
        if (self.totp_code is None) == (self.recovery_code is None):
            raise ValueError("Send either a code from your app or a recovery code.")
        return self


class CodeIn(Model):
    code: Code


class TotpSetupOut(Model):
    otpauth_uri: str
    secret: str


class TotpEnableOut(Model):
    recovery_codes: list[str]


class PasswordIn(Model):
    password: str = Field(max_length=MAX_LENGTH)

    @model_validator(mode="after")
    def _long_enough(self) -> PasswordIn:
        if len(self.password) < MIN_LENGTH:
            raise ValueError(f"Use at least {MIN_LENGTH} characters.")
        return self


class InviteAcceptIn(Model):
    token: Token


class InviteIn(Model):
    email: Email


class InviteOut(Model):
    invite_url: str


Outcome = Literal["success", "failure"]
