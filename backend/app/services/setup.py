"""First-run setup: claim an empty instance with a one-time setup token.

- On startup, while setup is pending, the API mints a random token, keeps only its sha256 in
  Redis (1h TTL) and logs the token once at WARNING (``app.main``). A restart mints a new one.
- Setup is *pending* until the owner holds a credential, so a browser closed between "create
  household" and "register passkey" can resume: the token holder re-runs ``POST /setup`` and
  gets a fresh session for the existing owner (no second household).
- States: ``fresh`` (no owner yet), ``pending`` (one owner, zero credentials), ``complete``.
"""

from __future__ import annotations

from typing import Literal

from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import apply_context, create_household, unscoped_session
from app.models import AppUser, Household, Person
from app.schemas.auth import SessionOut, SetupIn
from app.security.ratelimit import Locked, RateLimiter, ip_scope
from app.security.tokens import new_token, same_hash, token_hash_hex
from app.services import audit, sessions
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.users import has_any_credential, session_out

SETUP_KEY = "hf:setup:token"
SETUP_TTL_SECONDS = 3600
# pg_advisory_xact_lock key serializing concurrent POST /setup calls ("hfsetup" in ASCII).
SETUP_LOCK_KEY = 0x6866736574757000

SetupState = Literal["fresh", "pending", "complete"]


async def _state(s: AsyncSession) -> tuple[SetupState, AppUser | None]:
    owners = list((await s.execute(select(AppUser).where(AppUser.role == "owner"))).scalars())
    if not owners:
        return "fresh", None
    if len(owners) == 1 and not await has_any_credential(s, owners[0]):
        return "pending", owners[0]
    return "complete", None


async def setup_state(factory: async_sessionmaker[AsyncSession]) -> SetupState:
    async with unscoped_session(factory) as s:
        state, _ = await _state(s)
    return state


async def issue_setup_token(redis: Redis) -> str:
    """Mint a new setup token (replacing any previous one). Only its hash is stored."""
    token = new_token()
    await redis.set(SETUP_KEY, token_hash_hex(token), ex=SETUP_TTL_SECONDS)
    return token


async def clear_setup_token(redis: Redis) -> None:
    await redis.delete(SETUP_KEY)


async def _token_ok(redis: Redis, token: str) -> bool:
    stored = await redis.get(SETUP_KEY)
    if stored is None:
        return False
    expected = stored.decode() if isinstance(stored, bytes) else str(stored)
    return same_hash(expected, token_hash_hex(token))


async def run_setup(ctx: RequestContext, body: SetupIn) -> tuple[str, SessionOut]:
    limiter = RateLimiter(ctx.redis)
    attempt = await limiter.reserve(ip_scope(ctx.ip))
    if isinstance(attempt, Locked):
        raise ApiError(
            "rate_limited", attempt.message, headers={"Retry-After": str(attempt.retry_after)}
        )
    if await setup_state(ctx.factory) == "complete":
        await limiter.refund(attempt)
        raise ApiError("setup_complete", "This instance is already set up.")
    if not await _token_ok(ctx.redis, body.setup_token):
        # The reserved attempt stays counted as the failure.
        await audit.record(
            ctx.factory, "setup.attempt", household_id=None, ip=ctx.ip, outcome="failure"
        )
        raise ApiError("invalid_setup_token", "That setup token is wrong or expired.")

    try:
        async with unscoped_session(ctx.factory) as s:
            await s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SETUP_LOCK_KEY})
            state, owner = await _state(s)
            if state == "complete":
                raise ApiError("setup_complete", "This instance is already set up.")
            if state == "fresh":
                household_id = await create_household(s, body.household_name)
                await apply_context(s, household_id, None)
                person = Person(
                    household_id=household_id,
                    display_name=body.display_name,
                    filing_role="primary",
                )
                s.add(person)
                await s.flush()
                user = AppUser(
                    household_id=household_id,
                    person_id=person.id,
                    email=body.email,
                    role="owner",
                )
                s.add(user)
                action = "setup.complete"
            elif owner is not None:
                user = owner
                household_id = user.household_id
                await apply_context(s, household_id, None)
                household = (await s.execute(select(Household))).scalar_one()
                household.name = body.household_name
                if user.person_id is not None:
                    person = (
                        await s.execute(select(Person).where(Person.id == user.person_id))
                    ).scalar_one()
                    person.display_name = body.display_name
                user.email = body.email
                action = "setup.resumed"
            else:  # pending always carries its owner
                raise ApiError("setup_complete", "This instance is already set up.")
            await s.flush()
            token = await sessions.create(
                s, user.id, ip=ctx.ip, user_agent=ctx.user_agent, strong=False
            )
            audit.add(s, action, household_id=household_id, user_id=user.id, ip=ctx.ip)
            out = await session_out(s, user)
    except IntegrityError:
        raise ApiError("conflict", "That email already has an account.") from None
    await limiter.succeed(attempt)
    return token, out
