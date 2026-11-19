"""Opaque cookie sessions (``auth_session``). Only ``sha256(token)`` is stored.

- 12-hour absolute lifetime (no sliding renewal). Logout sets ``revoked_at``.
- Credential changes (passkey removed, TOTP off, password set) revoke the user's other sessions
  (:func:`revoke_others`).
- ``reauth_at`` marks the last strong authentication (passkey or password + TOTP). Sensitive
  actions need it within ``REAUTH_WINDOW``. Sessions started from a setup token or invite link
  begin with no ``reauth_at``: holding a link is not proof of a credential.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppUser, AuthSession
from app.security.client_ip import IPAddress
from app.security.tokens import new_token, token_hash

COOKIE_NAME = "hf_session"
SESSION_TTL = timedelta(hours=12)
REAUTH_WINDOW = timedelta(minutes=5)
TOUCH_EVERY = timedelta(minutes=1)
USER_AGENT_MAX = 512


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Principal:
    """The signed-in user behind a request. Carries ids only, never the token."""

    session_id: uuid.UUID
    user_id: uuid.UUID
    household_id: uuid.UUID
    person_id: uuid.UUID | None
    role: str
    email: str
    reauth_at: datetime | None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    def reauth_fresh(self, now: datetime | None = None) -> bool:
        if self.reauth_at is None:
            return False
        return (now or utcnow()) - self.reauth_at <= REAUTH_WINDOW


async def create(
    s: AsyncSession,
    user_id: uuid.UUID,
    *,
    ip: IPAddress | None,
    user_agent: str | None,
    strong: bool,
) -> str:
    """Insert a session and return the cookie value (the only time it exists in clear)."""
    token = new_token()
    now = utcnow()
    s.add(
        AuthSession(
            user_id=user_id,
            token_hash=token_hash(token),
            created_at=now,
            expires_at=now + SESSION_TTL,
            last_seen_at=now,
            reauth_at=now if strong else None,
            ip=ip,
            user_agent=(user_agent or "")[:USER_AGENT_MAX] or None,
        )
    )
    await s.flush()
    return token


async def resolve(s: AsyncSession, token: str) -> Principal | None:
    """The live session for ``token``: not revoked, not expired, user not disabled."""
    now = utcnow()
    row = (
        await s.execute(
            select(AuthSession, AppUser)
            .join(AppUser, AppUser.id == AuthSession.user_id)
            .where(
                AuthSession.token_hash == token_hash(token),
                AuthSession.revoked_at.is_(None),
                AuthSession.expires_at > now,
                AppUser.disabled_at.is_(None),
            )
        )
    ).first()
    if row is None:
        return None
    sess, user = row
    if sess.last_seen_at is None or now - sess.last_seen_at > TOUCH_EVERY:
        sess.last_seen_at = now
    return Principal(
        session_id=sess.id,
        user_id=user.id,
        household_id=user.household_id,
        person_id=user.person_id,
        role=user.role,
        email=user.email,
        reauth_at=sess.reauth_at,
    )


async def revoke(s: AsyncSession, token: str) -> uuid.UUID | None:
    """Revoke the session for ``token``. Returns its user id, if it was live."""
    sess = (
        await s.execute(
            select(AuthSession).where(
                AuthSession.token_hash == token_hash(token), AuthSession.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if sess is None:
        return None
    sess.revoked_at = utcnow()
    return sess.user_id


async def mark_reauth(s: AsyncSession, session_id: uuid.UUID) -> None:
    await s.execute(
        update(AuthSession).where(AuthSession.id == session_id).values(reauth_at=utcnow())
    )


async def revoke_others(s: AsyncSession, user_id: uuid.UUID, keep: uuid.UUID) -> int:
    """Revoke every live session of ``user_id`` except ``keep`` (after a credential change)."""
    result = await s.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user_id,
            AuthSession.id != keep,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow())
    )
    return int(getattr(result, "rowcount", 0) or 0)
