"""Invite links: let a person in the household create their user and register a passkey.

- 32-byte random token, shown once in the URL fragment (``{APP_BASE_URL}/invite#{token}``) so it
  never reaches proxy access logs. The base URL comes from settings, never the Host header.
- Redis holds ``sha256(token)`` -> invite for 24h. The token itself is never stored or logged.
- Replacing and accepting are each one Lua script. Two creates for the same person cannot both
  leave a live token key. Accept deletes the person index only when it still names the digest
  just consumed, so a newer invite keeps its index.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from app.db_context import scoped_session
from app.models import AppUser, Person
from app.schemas.auth import SessionOut
from app.security.ratelimit import Locked, RateLimiter, ip_scope
from app.security.tokens import new_token, token_hash_hex
from app.services import audit, sessions
from app.services.auth import require_fresh
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.household import require_owner
from app.services.sessions import Principal
from app.services.users import session_out

INVITE_TTL_SECONDS = 24 * 3600
_INVITE_PREFIX = "hf:invite:"
_PERSON_PREFIX = "hf:invite:person:"
INVALID = "This invite link is used, expired or wrong."
INVITE_UNUSABLE = "This invite can't be used. Ask for a new one."

# KEYS[1] person index, KEYS[2] new token key.
# ARGV: digest, payload, ttl seconds, token-key prefix.
# One script so two creates cannot both store a token key.
_REPLACE_INVITE = """
local digest = ARGV[1]
local previous = redis.call('GET', KEYS[1])
if type(previous) == 'string' and previous ~= digest and #previous == 64
    and string.match(previous, '^%x+$') then
  redis.call('DEL', ARGV[4] .. previous)
end
local ttl = math.floor(tonumber(ARGV[3]))
redis.call('SET', KEYS[2], ARGV[2], 'EX', ttl)
redis.call('SET', KEYS[1], digest, 'EX', ttl)
return 1
"""

# KEYS[1] token key. ARGV: digest, person-key prefix.
# Redis turns a missing key into false inside Lua. The person index is removed only when it
# still names this digest: a create that stored a newer invite must keep its index.
_CONSUME_INVITE = """
local digest = ARGV[1]
local raw = redis.call('GETDEL', KEYS[1])
if not raw then return false end
local ok, invite = pcall(cjson.decode, raw)
if ok and type(invite) == 'table' then
  local person_id = invite['person_id']
  local uuid = '^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$'
  if type(person_id) == 'string' and string.match(person_id, uuid) then
    local person_key = ARGV[2] .. person_id
    if redis.call('GET', person_key) == digest then
      redis.call('DEL', person_key)
    end
  end
end
return raw
"""


def _token_key(digest: str) -> str:
    return f"{_INVITE_PREFIX}{digest}"


def _person_key(person_id: uuid.UUID) -> str:
    return f"{_PERSON_PREFIX}{person_id}"


async def create_invite(
    ctx: RequestContext, principal: Principal, person_id: uuid.UUID, email: str
) -> str:
    require_owner(principal)
    require_fresh(principal)
    async with scoped_session(ctx.factory, principal.household_id, principal.person_id) as s:
        person = (
            await s.execute(select(Person).where(Person.id == person_id))
        ).scalar_one_or_none()
        if person is None:
            raise ApiError("not_found", "No such person.")
        if (await s.execute(select(exists().where(AppUser.person_id == person_id)))).scalar_one():
            raise ApiError("conflict", "This person already has an account.")
        # Only this household's users: an email taken in another household must not be
        # revealed here. Such an invite fails generically when accepted (unique index).
        taken = exists().where(
            AppUser.email == email, AppUser.household_id == principal.household_id
        )
        if (await s.execute(select(taken))).scalar_one():
            raise ApiError("conflict", "That email already has an account.")
        token = new_token()
        digest = token_hash_hex(token)
        payload = json.dumps(
            {
                "household_id": str(principal.household_id),
                "person_id": str(person_id),
                "email": email,
                "invited_by": str(principal.user_id),
            }
        )
        await ctx.redis.register_script(_REPLACE_INVITE)(
            keys=[_person_key(person_id), _token_key(digest)],
            args=[digest, payload, str(INVITE_TTL_SECONDS), _INVITE_PREFIX],
        )
        audit.add(
            s,
            "household.invite.created",
            household_id=principal.household_id,
            user_id=principal.user_id,
            ip=ctx.ip,
            entity="person",
            entity_id=person_id,
        )
    base = ctx.settings.app_base_url.rstrip("/")
    return f"{base}/invite#{token}"


async def accept_invite(ctx: RequestContext, token: str) -> tuple[str, SessionOut]:
    limiter = RateLimiter(ctx.redis)
    attempt = await limiter.reserve(ip_scope(ctx.ip))
    if isinstance(attempt, Locked):
        raise ApiError(
            "rate_limited", attempt.message, headers={"Retry-After": str(attempt.retry_after)}
        )
    digest = token_hash_hex(token)
    raw: object = await ctx.redis.register_script(_CONSUME_INVITE)(
        keys=[_token_key(digest)],
        args=[digest, _PERSON_PREFIX],
    )
    if not isinstance(raw, (bytes, str)):
        # The reserved attempt stays counted as the failure.
        await audit.record(
            ctx.factory,
            "household.invite.accepted",
            household_id=None,
            ip=ctx.ip,
            outcome="failure",
        )
        raise ApiError("invalid_invite", INVALID)
    # A real token was presented: the attempt was not a guess.
    await limiter.succeed(attempt)
    invite = json.loads(raw)
    household_id = uuid.UUID(invite["household_id"])
    person_id = uuid.UUID(invite["person_id"])
    try:
        async with scoped_session(ctx.factory, household_id, person_id) as s:
            person = (
                await s.execute(select(Person).where(Person.id == person_id))
            ).scalar_one_or_none()
            if person is None:
                raise ApiError("invalid_invite", INVALID)
            user = AppUser(
                household_id=household_id,
                person_id=person_id,
                email=invite["email"],
                role="member",
            )
            s.add(user)
            await s.flush()
            session_token = await sessions.create(
                s, user.id, ip=ctx.ip, user_agent=ctx.user_agent, strong=False
            )
            audit.add(
                s,
                "household.invite.accepted",
                household_id=household_id,
                user_id=user.id,
                ip=ctx.ip,
                entity="person",
                entity_id=person_id,
            )
            out = await session_out(s, user)
    except IntegrityError:
        # The person or the email got an account after the invite was sent, or the email
        # belongs to someone in another household. Say nothing about which, or where.
        raise ApiError("conflict", INVITE_UNUSABLE) from None
    return session_token, out
