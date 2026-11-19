"""Redis rate limiting with exponential lockout for login and step-up attempts.

Attempts are *reserved* before anything is verified (ADR 0010, "Hardening"): one Lua script
checks every scope's lock and, only if none is locked, increments every scope's counter and sets
the lock on any scope that reached its threshold. Check and count are one atomic step, so N
parallel requests can't all slip past a check that ran before any of them failed.

- A reserved attempt counts as a failure unless the caller settles it as a success (or refunds
  it because nothing was actually verified).
- Success clears account scopes (``acct:``) and refunds the other scopes (the IP may be shared;
  clearing it would let one good login reset an attacker's counter).
- Account scope = sha256 of the lower-case email, whether or not the account exists, so lockout
  behaviour reveals nothing. After the threshold within ``WINDOW_SECONDS`` the scope is locked
  for ``BASE_LOCK_SECONDS``, doubling with every further attempt up to ``MAX_LOCK_SECONDS``.
- :meth:`RateLimiter.throttle` is a plain fixed-window counter for unauthenticated endpoints that
  only mint state (e.g. passkey login options).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from redis.asyncio import Redis

PREFIX = "hf:rl"
ACCOUNT_THRESHOLD = 5
# IPs get more room than accounts: several people can share one address.
IP_THRESHOLD = 20
WINDOW_SECONDS = 24 * 3600
BASE_LOCK_SECONDS = 60
MAX_LOCK_SECONDS = 3600

# KEYS: (fail, lock) per scope. ARGV: window, base, max, then one threshold per scope.
# Returns {0, retry_after} when any scope is locked (nothing counted), else {1, created...} with
# one 0/1 per scope: whether this attempt set that scope's lock.
_RESERVE = """
local n = #KEYS / 2
local worst = 0
for i = 1, n do
  local ttl = redis.call('TTL', KEYS[2 * i])
  if ttl > worst then worst = ttl end
end
if worst > 0 then return {0, worst} end
local window = tonumber(ARGV[1])
local base = tonumber(ARGV[2])
local cap = tonumber(ARGV[3])
local out = {1}
for i = 1, n do
  local c = redis.call('INCR', KEYS[2 * i - 1])
  if c == 1 then redis.call('EXPIRE', KEYS[2 * i - 1], window) end
  local threshold = tonumber(ARGV[3 + i])
  if c >= threshold then
    local secs = cap
    local exp = c - threshold
    if exp < 32 then secs = math.min(base * (2 ^ exp), cap) end
    redis.call('SET', KEYS[2 * i], '1', 'EX', math.floor(secs))
    out[i + 1] = 1
  else
    out[i + 1] = 0
  end
end
return out
"""

# KEYS: (fail, lock) per scope. ARGV: one 0/1 per scope (did the reservation set the lock).
_REFUND = """
for i = 1, #KEYS / 2 do
  if redis.call('EXISTS', KEYS[2 * i - 1]) == 1 then
    if redis.call('DECR', KEYS[2 * i - 1]) <= 0 then redis.call('DEL', KEYS[2 * i - 1]) end
  end
  if ARGV[i] == '1' then redis.call('DEL', KEYS[2 * i]) end
end
return 1
"""

# KEYS[1]: counter. ARGV: limit, window. Returns 0 if allowed, else seconds until the reset.
_THROTTLE = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) end
if c > tonumber(ARGV[1]) then
  local ttl = redis.call('TTL', KEYS[1])
  if ttl < 1 then ttl = 1 end
  return ttl
end
return 0
"""


@dataclass(frozen=True)
class Locked:
    retry_after: int

    @property
    def message(self) -> str:
        minutes = max(1, math.ceil(self.retry_after / 60))
        unit = "minute" if minutes == 1 else "minutes"
        return f"Too many attempts. Try again in {minutes} {unit}."


@dataclass(frozen=True)
class Attempt:
    """A reserved attempt. Counts as a failure unless settled with succeed() or refund()."""

    scopes: tuple[str, ...]
    created_locks: tuple[bool, ...]


def account_scope(email: str) -> str:
    return "acct:" + hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def ip_scope(ip: object) -> str:
    return f"ip:{ip}"


def threshold_for(scope: str) -> int:
    return ACCOUNT_THRESHOLD if scope.startswith("acct:") else IP_THRESHOLD


def lock_seconds(failures: int, threshold: int) -> int:
    if failures < threshold:
        return 0
    return int(min(BASE_LOCK_SECONDS * 2 ** (failures - threshold), MAX_LOCK_SECONDS))


def _keys(scopes: tuple[str, ...]) -> list[str]:
    keys: list[str] = []
    for scope in scopes:
        keys += [f"{PREFIX}:fail:{scope}", f"{PREFIX}:lock:{scope}"]
    return keys


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def reserve(self, *scopes: str) -> Attempt | Locked:
        """Atomically refuse if any scope is locked, else count one attempt on every scope."""
        numbers = [WINDOW_SECONDS, BASE_LOCK_SECONDS, MAX_LOCK_SECONDS]
        numbers += [threshold_for(s) for s in scopes]
        args = [str(n) for n in numbers]
        result = await self._redis.eval(_RESERVE, len(scopes) * 2, *_keys(scopes), *args)  # type: ignore[misc]
        values = [int(v) for v in result]
        if values[0] == 0:
            return Locked(max(1, values[1]))
        return Attempt(scopes, tuple(v == 1 for v in values[1:]))

    async def succeed(self, attempt: Attempt) -> None:
        """Clear account scopes; refund the rest."""
        accounts = [s for s in attempt.scopes if s.startswith("acct:")]
        if accounts:
            await self.clear(*accounts)
        await self._refund(attempt, only_non_account=True)

    async def refund(self, attempt: Attempt) -> None:
        """Nothing was verified (e.g. the instance is already set up): uncount the attempt."""
        await self._refund(attempt, only_non_account=False)

    async def _refund(self, attempt: Attempt, *, only_non_account: bool) -> None:
        pairs = [
            (scope, created)
            for scope, created in zip(attempt.scopes, attempt.created_locks, strict=True)
            if not (only_non_account and scope.startswith("acct:"))
        ]
        if not pairs:
            return
        scopes = tuple(p[0] for p in pairs)
        flags = ["1" if p[1] else "0" for p in pairs]
        await self._redis.eval(_REFUND, len(scopes) * 2, *_keys(scopes), *flags)  # type: ignore[misc]

    async def clear(self, *scopes: str) -> None:
        if scopes:
            await self._redis.delete(*_keys(tuple(scopes)))

    async def throttle(self, scope: str, *, limit: int, window: int) -> Locked | None:
        """Fixed window: at most ``limit`` calls per ``window`` seconds for ``scope``."""
        key = f"{PREFIX}:throttle:{scope}"
        retry = int(await self._redis.eval(_THROTTLE, 1, key, str(limit), str(window)))  # type: ignore[misc]
        return Locked(retry) if retry > 0 else None
