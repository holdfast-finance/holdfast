"""Request dependencies: service context, the current principal, and the session cookie."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import unscoped_session
from app.security.client_ip import client_ip
from app.services import sessions
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.sessions import COOKIE_NAME, SESSION_TTL, Principal


def get_context(request: Request) -> RequestContext:
    return RequestContext(
        factory=request.app.state.session_factory,
        redis=request.app.state.redis,
        settings=request.app.state.settings,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


async def get_principal(request: Request) -> Principal:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 128:
        raise ApiError("unauthenticated", "Sign in first.")
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with unscoped_session(factory) as s:
        principal = await sessions.resolve(s, token)
    if principal is None:
        raise ApiError("unauthenticated", "Your session ended. Sign in again.")
    return principal


Ctx = Annotated[RequestContext, Depends(get_context)]
CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
