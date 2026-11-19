"""What a service call needs from the request, without depending on FastAPI."""

from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.security.client_ip import IPAddress


@dataclass(frozen=True)
class RequestContext:
    factory: async_sessionmaker[AsyncSession]
    redis: Redis
    settings: Settings
    ip: IPAddress | None
    user_agent: str | None
