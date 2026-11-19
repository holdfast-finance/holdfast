"""``/api/auth/*``: passkeys, password + TOTP fallback, step-up, logout, me."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.api.deps import Ctx, CurrentPrincipal, clear_session_cookie, set_session_cookie
from app.schemas.auth import (
    CodeIn,
    MeOut,
    PasskeyAssertionIn,
    PasskeyList,
    PasskeyRegisterOptionsIn,
    PasskeyRegisterVerifyIn,
    PasskeyRegisterVerifyOut,
    PasswordIn,
    PasswordLoginIn,
    TotpEnableOut,
    TotpSetupOut,
    UserResponse,
)
from app.services.auth import AuthService
from app.services.sessions import COOKIE_NAME

router = APIRouter(prefix="/auth", tags=["auth"])
NO_CONTENT = status.HTTP_204_NO_CONTENT


@router.get("/me")
async def me(ctx: Ctx, principal: CurrentPrincipal) -> MeOut:
    return await AuthService(ctx).me(principal)


@router.post("/logout", status_code=NO_CONTENT)
async def logout(request: Request, response: Response, ctx: Ctx) -> None:
    await AuthService(ctx).logout(request.cookies.get(COOKIE_NAME))
    clear_session_cookie(response)


# --------------------------------------------------------------------------- passkeys


@router.post("/passkey/register/options")
async def passkey_register_options(
    ctx: Ctx, principal: CurrentPrincipal, _body: PasskeyRegisterOptionsIn | None = None
) -> dict[str, Any]:
    return await AuthService(ctx).passkey_register_options(principal)


@router.post("/passkey/register/verify")
async def passkey_register_verify(
    body: PasskeyRegisterVerifyIn, ctx: Ctx, principal: CurrentPrincipal
) -> PasskeyRegisterVerifyOut:
    cred = await AuthService(ctx).passkey_register(principal, body.credential, body.nickname)
    return PasskeyRegisterVerifyOut(credential=cred)


@router.post("/passkey/login/options")
async def passkey_login_options(ctx: Ctx) -> dict[str, Any]:
    return await AuthService(ctx).passkey_login_options()


@router.post("/passkey/login/verify")
async def passkey_login_verify(
    body: PasskeyAssertionIn, response: Response, ctx: Ctx
) -> UserResponse:
    token, user = await AuthService(ctx).passkey_login(body.credential)
    set_session_cookie(response, token)
    return UserResponse(user=user)


@router.get("/passkeys")
async def list_passkeys(ctx: Ctx, principal: CurrentPrincipal) -> PasskeyList:
    return await AuthService(ctx).list_passkeys(principal)


@router.delete("/passkeys/{passkey_id}", status_code=NO_CONTENT)
async def delete_passkey(passkey_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal) -> None:
    await AuthService(ctx).delete_passkey(principal, passkey_id)


# --------------------------------------------------------------------------- password + TOTP


@router.post("/password/login")
async def password_login(body: PasswordLoginIn, response: Response, ctx: Ctx) -> UserResponse:
    token, user = await AuthService(ctx).password_login(body)
    set_session_cookie(response, token)
    return UserResponse(user=user)


@router.post("/password", status_code=NO_CONTENT)
async def set_password(body: PasswordIn, ctx: Ctx, principal: CurrentPrincipal) -> None:
    await AuthService(ctx).set_password(principal, body.password)


@router.post("/totp/setup")
async def totp_setup(ctx: Ctx, principal: CurrentPrincipal) -> TotpSetupOut:
    return await AuthService(ctx).totp_setup(principal)


@router.post("/totp/enable")
async def totp_enable(body: CodeIn, ctx: Ctx, principal: CurrentPrincipal) -> TotpEnableOut:
    return TotpEnableOut(recovery_codes=await AuthService(ctx).totp_enable(principal, body.code))


@router.post("/totp/disable", status_code=NO_CONTENT)
async def totp_disable(ctx: Ctx, principal: CurrentPrincipal) -> None:
    await AuthService(ctx).totp_disable(principal)


# --------------------------------------------------------------------------- step-up


@router.post("/reauth/passkey/options")
async def reauth_passkey_options(ctx: Ctx, principal: CurrentPrincipal) -> dict[str, Any]:
    return await AuthService(ctx).reauth_passkey_options(principal)


@router.post("/reauth/passkey/verify", status_code=NO_CONTENT)
async def reauth_passkey_verify(
    body: PasskeyAssertionIn, ctx: Ctx, principal: CurrentPrincipal
) -> None:
    await AuthService(ctx).reauth_passkey(principal, body.credential)


@router.post("/reauth/totp", status_code=NO_CONTENT)
async def reauth_totp(body: CodeIn, ctx: Ctx, principal: CurrentPrincipal) -> None:
    await AuthService(ctx).reauth_totp(principal, body.code)
