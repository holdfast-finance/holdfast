"""Identity tables: users, passkeys, sessions and TOTP recovery codes.

These are read **before** a household context exists (login), so they have no RLS. Only
``app/services/auth*`` code should touch them, through ``app.db_context.unscoped_session``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, text
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import USER_ROLES, UUIDPK, CreatedAt, check_in


class AppUser(Base):
    """A login. ``email`` is stored lower-cased (CHECK) so the plain UNIQUE is case-insensitive.

    ``household_id`` (migration 0003) is how login finds the tenant: ``person`` has RLS, so an
    unscoped session can't read ``person.household_id``. The composite FK keeps it equal to the
    person's household whenever ``person_id`` is set.
    """

    __tablename__ = "app_user"
    __table_args__ = (
        check_in("role", USER_ROLES),
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        ForeignKeyConstraint(
            ["person_id", "household_id"],
            ["person.id", "person.household_id"],
            name="fk_app_user_person_household",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("household.id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), unique=True
    )
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str | None]  # argon2id
    totp_secret_enc: Mapped[bytes | None]  # Fernet ciphertext
    totp_enabled: Mapped[bool] = mapped_column(server_default=text("false"))
    role: Mapped[str] = mapped_column(server_default=text("'member'"))
    created_at: Mapped[CreatedAt]
    last_login_at: Mapped[datetime | None]
    disabled_at: Mapped[datetime | None]


class WebauthnCredential(Base):
    __tablename__ = "webauthn_credential"

    id: Mapped[UUIDPK]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    credential_id: Mapped[bytes] = mapped_column(unique=True)
    public_key: Mapped[bytes]
    sign_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    transports: Mapped[list[str]] = mapped_column(server_default=text("'{}'"))
    aaguid: Mapped[uuid.UUID | None]
    backup_eligible: Mapped[bool] = mapped_column(server_default=text("false"))
    backup_state: Mapped[bool] = mapped_column(server_default=text("false"))
    nickname: Mapped[str | None]
    created_at: Mapped[CreatedAt]
    last_used_at: Mapped[datetime | None]


class AuthSession(Base):
    """Opaque session. Only ``sha256(token)`` is stored, never the cookie value."""

    __tablename__ = "auth_session"

    id: Mapped[UUIDPK]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[bytes] = mapped_column(unique=True)
    created_at: Mapped[CreatedAt]
    expires_at: Mapped[datetime]
    last_seen_at: Mapped[datetime | None]
    reauth_at: Mapped[datetime | None]
    ip: Mapped[IPv4Address | IPv6Address | None] = mapped_column(INET)
    user_agent: Mapped[str | None]
    revoked_at: Mapped[datetime | None]


class TotpRecoveryCode(Base):
    __tablename__ = "totp_recovery_code"

    id: Mapped[UUIDPK]
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str]
    used_at: Mapped[datetime | None]
