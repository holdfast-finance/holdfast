"""Redis rate limiting with exponential lockout for login and step-up attempts.

Two scopes are counted on every failure: the client IP and the account (sha256 of the lower-case
email, whether or not that account exists, so lockout behaviour reveals nothing). After the
threshold within ``WINDOW_SECONDS`` the scope is locked for ``BASE_LOCK_SECONDS``, doubling with
every further failure up to ``MAX_LOCK_SECONDS``. A success clears the account scope only (an IP
may be shared, and clearing it would let one good login reset an attacker's counter).
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


@dataclass(frozen=True)
class Locked:
    retry_after: int

    @property
    def message(self) -> str:
        minutes = max(1, math.ceil(self.retry_after / 60))
        unit = "minute" if minutes == 1 else "minutes"
        return f"Too many attempts. Try again in {minutes} {unit}."


def account_scope(email: str) -> str:
    return "acct:" + hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def ip_scope(ip: object) -> str:
    return f"ip:{ip}"


def lock_seconds(failures: int, threshold: int) -> int:
    if failures < threshold:
        return 0
    return int(min(BASE_LOCK_SECONDS * 2 ** (failures - threshold), MAX_LOCK_SECONDS))


class RateLimiter:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def check(self, *scopes: str) -> Locked | None:
        """The longest active lock among ``scopes``, if any."""
        worst = 0
        for scope in scopes:
            ttl = int(await self._redis.ttl(f"{PREFIX}:lock:{scope}"))
            worst = max(worst, ttl)
        return Locked(worst) if worst > 0 else None

    async def fail(self, *scopes: str) -> None:
        for scope in scopes:
            threshold = IP_THRESHOLD if scope.startswith("ip:") else ACCOUNT_THRESHOLD
            key = f"{PREFIX}:fail:{scope}"
            count = int(await self._redis.incr(key))
            if count == 1:
                await self._redis.expire(key, WINDOW_SECONDS)
            seconds = lock_seconds(count, threshold)
            if seconds:
                await self._redis.set(f"{PREFIX}:lock:{scope}", "1", ex=seconds)

    async def succeed(self, *scopes: str) -> None:
        for scope in scopes:
            await self._redis.delete(f"{PREFIX}:fail:{scope}", f"{PREFIX}:lock:{scope}")
