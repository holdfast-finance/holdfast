"""Login, step-up and credential management (passkeys, password, TOTP).

Rules (Phase 1 contract + security review amendments):

- Login: passkey, or email + password + (TOTP code | recovery code). Every failure answers with
  the same error, costs one argon2 verification, and is counted by the rate limiter.
- Step-up (``reauth_at`` within 5 minutes) for: removing a passkey, turning TOTP off, setting a
  password, inviting, and *adding* a credential (passkey or TOTP) once the user has one. A
  session with zero credentials (fresh from setup or an invite) may add its first one.
- A user is never left without a way in: the last passkey can go only if TOTP *and* a password
  are set, and TOTP can be turned off only while a passkey exists.
- Never logged or audited: tokens, codes, secrets, passwords.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session, unscoped_session
from app.models import AppUser, Person, TotpRecoveryCode, WebauthnCredential
from app.schemas.auth import (
    CredentialOut,
    MeOut,
    PasskeyList,
    PasskeyOut,
    PasswordLoginIn,
    TotpSetupOut,
    UserOut,
)
from app.security import crypto, passwords, totp
from app.security.ratelimit import RateLimiter, account_scope, ip_scope
from app.services import audit, sessions
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.passkeys import (
    VerificationFailed,
    WebAuthnService,
    credential_id_of,
    user_handle_of,
)
from app.services.sessions import Principal
from app.services.setup import clear_setup_token
from app.services.users import get_user, has_any_credential, has_passkey, session_out, user_out

TOTP_REPLAY_TTL_SECONDS = 120
DEFAULT_NICKNAME = "Passkey"

INVALID_CREDENTIALS = "Email, password or code is wrong."
PASSKEY_FAILED = "That passkey could not be verified."
INVALID_CODE = "That code did not match. Check the time on your device."


def _rate_limited(retry_after: int, message: str) -> ApiError:
    return ApiError("rate_limited", message, headers={"Retry-After": str(retry_after)})


def _reauth_required() -> ApiError:
    return ApiError("reauth_required", "Confirm it is you to continue.")


def require_fresh(principal: Principal) -> None:
    if not principal.reauth_fresh():
        raise _reauth_required()


class AuthService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.limiter = RateLimiter(ctx.redis)
        self.webauthn = WebAuthnService(ctx.settings, ctx.redis)

    # ------------------------------------------------------------------ helpers

    def _scoped(self, principal: Principal) -> Any:
        return scoped_session(self.ctx.factory, principal.household_id, principal.person_id)

    async def _audit(self, s: AsyncSession, principal: Principal, action: str, **kw: Any) -> None:
        audit.add(
            s,
            action,
            household_id=principal.household_id,
            user_id=principal.user_id,
            ip=self.ctx.ip,
            **kw,
        )

    async def _totp_matches(self, user: AppUser, code: str) -> bool:
        """Check ``code`` against the user's secret; each time step is accepted only once."""
        if user.totp_secret_enc is None:
            return False
        secret = crypto.decrypt(user.totp_secret_enc.decode("ascii"))
        step = totp.matching_step(secret, code)
        if step is None:
            return False
        fresh = await self.ctx.redis.set(
            f"hf:totp:used:{user.id}:{step}", "1", nx=True, ex=TOTP_REPLAY_TTL_SECONDS
        )
        return bool(fresh)

    async def _consume_recovery_code(self, s: AsyncSession, user: AppUser, code: str) -> bool:
        result = await s.execute(
            update(TotpRecoveryCode)
            .where(
                TotpRecoveryCode.user_id == user.id,
                TotpRecoveryCode.code_hash == totp.recovery_code_hash(code),
                TotpRecoveryCode.used_at.is_(None),
            )
            .values(used_at=func.now())
        )
        return bool(getattr(result, "rowcount", 0) == 1)

    async def _gate_new_credential(self, s: AsyncSession, principal: Principal) -> AppUser:
        user = await get_user(s, principal.user_id)
        if await has_any_credential(s, user):
            require_fresh(principal)
        return user

    # ------------------------------------------------------------------ session views

    async def me(self, principal: Principal) -> MeOut:
        async with self._scoped(principal) as s:
            user = await get_user(s, principal.user_id)
            out = await session_out(s, user)
        return MeOut(user=out.user, household=out.household, reauth_fresh=principal.reauth_fresh())

    async def logout(self, token: str | None) -> None:
        if not token:
            return
        async with unscoped_session(self.ctx.factory) as s:
            principal = await sessions.resolve(s, token)
            await sessions.revoke(s, token)
        if principal is not None:
            await audit.record(
                self.ctx.factory,
                "auth.logout",
                household_id=principal.household_id,
                user_id=principal.user_id,
                ip=self.ctx.ip,
            )

    # ------------------------------------------------------------------ password login

    async def password_login(self, body: PasswordLoginIn) -> tuple[str, UserOut]:
        email = body.email.strip().lower()
        scopes = (ip_scope(self.ctx.ip), account_scope(email))
        if locked := await self.limiter.check(*scopes):
            await audit.record(
                self.ctx.factory,
                "auth.login.password",
                household_id=None,
                ip=self.ctx.ip,
                outcome="failure",
                detail={"reason": "rate_limited"},
            )
            raise _rate_limited(locked.retry_after, locked.message)

        token: str | None = None
        user: AppUser | None = None
        async with unscoped_session(self.ctx.factory) as s:
            user = (
                await s.execute(
                    select(AppUser).where(AppUser.email == email, AppUser.disabled_at.is_(None))
                )
            ).scalar_one_or_none()
            ok = passwords.verify_password(user.password_hash if user else None, body.password)
            if ok and user is not None and user.totp_enabled:
                if body.totp_code is not None:
                    ok = await self._totp_matches(user, body.totp_code)
                elif body.recovery_code is not None:
                    ok = await self._consume_recovery_code(s, user, body.recovery_code)
                else:
                    ok = False
            else:
                ok = False  # no user, wrong password, or no second factor configured
            if ok and user is not None:
                if user.password_hash and passwords.needs_rehash(user.password_hash):
                    user.password_hash = passwords.hash_password(body.password)
                user.last_login_at = sessions.utcnow()
                token = await sessions.create(
                    s, user.id, ip=self.ctx.ip, user_agent=self.ctx.user_agent, strong=True
                )

        if token is None or user is None:
            await self.limiter.fail(*scopes)
            await audit.record(
                self.ctx.factory,
                "auth.login.password",
                household_id=user.household_id if user else None,
                user_id=user.id if user else None,
                ip=self.ctx.ip,
                outcome="failure",
            )
            raise ApiError("invalid_credentials", INVALID_CREDENTIALS)

        await self.limiter.succeed(scopes[1])
        async with scoped_session(self.ctx.factory, user.household_id, user.person_id) as s:
            audit.add(
                s,
                "auth.login.password",
                household_id=user.household_id,
                user_id=user.id,
                ip=self.ctx.ip,
                detail={"second_factor": "totp" if body.totp_code else "recovery_code"},
            )
            out = await user_out(s, user)
        return token, out

    # ------------------------------------------------------------------ passkey login

    async def passkey_login_options(self) -> dict[str, Any]:
        return await self.webauthn.authentication_options("login", bound_to="login", allow=[])

    async def passkey_login(self, credential: dict[str, Any]) -> tuple[str, UserOut]:
        ip = ip_scope(self.ctx.ip)
        if locked := await self.limiter.check(ip):
            raise _rate_limited(locked.retry_after, locked.message)

        token: str | None = None
        user: AppUser | None = None
        try:
            challenge = await self.webauthn.take_challenge("login", credential, "login")
            cred_id = credential_id_of(credential)
            async with unscoped_session(self.ctx.factory) as s:
                row = (
                    await s.execute(
                        select(WebauthnCredential, AppUser)
                        .join(AppUser, AppUser.id == WebauthnCredential.user_id)
                        .where(WebauthnCredential.credential_id == cred_id)
                        .with_for_update(of=WebauthnCredential)
                    )
                ).first()
                if row is None:
                    raise VerificationFailed
                cred, user = row
                if locked := await self.limiter.check(account_scope(user.email)):
                    raise _rate_limited(locked.retry_after, locked.message)
                if user.disabled_at is not None:
                    raise VerificationFailed
                handle = user_handle_of(credential)
                if handle is not None and handle != user.id.bytes:
                    raise VerificationFailed
                assertion = self.webauthn.verify_assertion(
                    credential=credential,
                    challenge=challenge,
                    public_key=cred.public_key,
                    sign_count=cred.sign_count,
                )
                now = sessions.utcnow()
                cred.sign_count = assertion.new_sign_count
                cred.backup_state = assertion.backup_state
                cred.last_used_at = now
                user.last_login_at = now
                token = await sessions.create(
                    s, user.id, ip=self.ctx.ip, user_agent=self.ctx.user_agent, strong=True
                )
        except VerificationFailed:
            scopes = [ip] if user is None else [ip, account_scope(user.email)]
            await self.limiter.fail(*scopes)
            await audit.record(
                self.ctx.factory,
                "auth.login.passkey",
                household_id=user.household_id if user else None,
                user_id=user.id if user else None,
                ip=self.ctx.ip,
                outcome="failure",
            )
            raise ApiError("passkey_verification_failed", PASSKEY_FAILED) from None

        if token is None or user is None:  # unreachable: every failure path raised above
            raise ApiError("passkey_verification_failed", PASSKEY_FAILED)
        await self.limiter.succeed(account_scope(user.email))
        async with scoped_session(self.ctx.factory, user.household_id, user.person_id) as s:
            audit.add(
                s,
                "auth.login.passkey",
                household_id=user.household_id,
                user_id=user.id,
                ip=self.ctx.ip,
            )
            out = await user_out(s, user)
        return token, out

    # ------------------------------------------------------------------ step-up

    async def reauth_passkey_options(self, principal: Principal) -> dict[str, Any]:
        async with unscoped_session(self.ctx.factory) as s:
            allow = list(
                (
                    await s.execute(
                        select(WebauthnCredential.credential_id).where(
                            WebauthnCredential.user_id == principal.user_id
                        )
                    )
                ).scalars()
            )
        return await self.webauthn.authentication_options(
            "reauth", bound_to=str(principal.session_id), allow=allow
        )

    async def reauth_passkey(self, principal: Principal, credential: dict[str, Any]) -> None:
        scopes = (ip_scope(self.ctx.ip), account_scope(principal.email))
        if locked := await self.limiter.check(*scopes):
            raise _rate_limited(locked.retry_after, locked.message)
        try:
            challenge = await self.webauthn.take_challenge(
                "reauth", credential, str(principal.session_id)
            )
            cred_id = credential_id_of(credential)
            async with self._scoped(principal) as s:
                cred = (
                    await s.execute(
                        select(WebauthnCredential)
                        .where(
                            WebauthnCredential.credential_id == cred_id,
                            WebauthnCredential.user_id == principal.user_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if cred is None:
                    raise VerificationFailed
                assertion = self.webauthn.verify_assertion(
                    credential=credential,
                    challenge=challenge,
                    public_key=cred.public_key,
                    sign_count=cred.sign_count,
                )
                cred.sign_count = assertion.new_sign_count
                cred.backup_state = assertion.backup_state
                cred.last_used_at = sessions.utcnow()
                await sessions.mark_reauth(s, principal.session_id)
                await self._audit(s, principal, "auth.reauth.passkey")
        except VerificationFailed:
            await self.limiter.fail(*scopes)
            await audit.record(
                self.ctx.factory,
                "auth.reauth.passkey",
                household_id=principal.household_id,
                user_id=principal.user_id,
                ip=self.ctx.ip,
                outcome="failure",
            )
            raise ApiError("passkey_verification_failed", PASSKEY_FAILED) from None
        await self.limiter.succeed(scopes[1])

    async def reauth_totp(self, principal: Principal, code: str) -> None:
        """TOTP only: recovery codes are kept for signing in after losing a device."""
        scopes = (ip_scope(self.ctx.ip), account_scope(principal.email))
        if locked := await self.limiter.check(*scopes):
            raise _rate_limited(locked.retry_after, locked.message)
        ok = False
        async with self._scoped(principal) as s:
            user = await get_user(s, principal.user_id)
            if user.totp_enabled and await self._totp_matches(user, code):
                ok = True
                await sessions.mark_reauth(s, principal.session_id)
                await self._audit(s, principal, "auth.reauth.totp")
        if not ok:
            await self.limiter.fail(*scopes)
            await audit.record(
                self.ctx.factory,
                "auth.reauth.totp",
                household_id=principal.household_id,
                user_id=principal.user_id,
                ip=self.ctx.ip,
                outcome="failure",
            )
            raise ApiError("invalid_code", INVALID_CODE)
        await self.limiter.succeed(scopes[1])

    # ------------------------------------------------------------------ passkey registration

    async def passkey_register_options(self, principal: Principal) -> dict[str, Any]:
        async with self._scoped(principal) as s:
            user = await self._gate_new_credential(s, principal)
            existing = list(
                (
                    await s.execute(
                        select(WebauthnCredential.credential_id).where(
                            WebauthnCredential.user_id == user.id
                        )
                    )
                ).scalars()
            )
            display_name = user.email
            if user.person_id is not None:
                name = (
                    await s.execute(select(Person.display_name).where(Person.id == user.person_id))
                ).scalar_one_or_none()
                display_name = name or user.email
        return await self.webauthn.registration_options(
            session_id=principal.session_id,
            user_id=user.id,
            user_name=user.email,
            display_name=display_name,
            existing=existing,
        )

    async def passkey_register(
        self, principal: Principal, credential: dict[str, Any], nickname: str | None
    ) -> CredentialOut:
        nickname = (nickname or "").strip()[:100] or DEFAULT_NICKNAME
        try:
            async with self._scoped(principal) as s:
                user = await self._gate_new_credential(s, principal)
                new = await self.webauthn.verify_registration(
                    session_id=principal.session_id, credential=credential
                )
                row = WebauthnCredential(
                    user_id=user.id,
                    credential_id=new.credential_id,
                    public_key=new.public_key,
                    sign_count=new.sign_count,
                    transports=new.transports,
                    aaguid=new.aaguid,
                    backup_eligible=new.backup_eligible,
                    backup_state=new.backup_state,
                    nickname=nickname,
                )
                s.add(row)
                await s.flush()
                await s.refresh(row)
                await self._audit(
                    s,
                    principal,
                    "auth.passkey.registered",
                    entity="webauthn_credential",
                    entity_id=row.id,
                )
                out = CredentialOut(id=row.id, nickname=nickname, created_at=row.created_at)
        except (VerificationFailed, IntegrityError):
            await audit.record(
                self.ctx.factory,
                "auth.passkey.registered",
                household_id=principal.household_id,
                user_id=principal.user_id,
                ip=self.ctx.ip,
                outcome="failure",
            )
            raise ApiError("passkey_verification_failed", PASSKEY_FAILED) from None
        if principal.is_owner:
            await clear_setup_token(self.ctx.redis)
        return out

    async def list_passkeys(self, principal: Principal) -> PasskeyList:
        async with unscoped_session(self.ctx.factory) as s:
            rows = (
                await s.execute(
                    select(WebauthnCredential)
                    .where(WebauthnCredential.user_id == principal.user_id)
                    .order_by(WebauthnCredential.created_at)
                )
            ).scalars()
            items = [
                PasskeyOut(
                    id=c.id,
                    nickname=c.nickname or DEFAULT_NICKNAME,
                    created_at=c.created_at,
                    last_used_at=c.last_used_at,
                )
                for c in rows
            ]
        return PasskeyList(items=items)

    async def delete_passkey(self, principal: Principal, passkey_id: uuid.UUID) -> None:
        require_fresh(principal)
        async with self._scoped(principal) as s:
            user = await get_user(s, principal.user_id, for_update=True)
            cred = (
                await s.execute(
                    select(WebauthnCredential).where(
                        WebauthnCredential.id == passkey_id,
                        WebauthnCredential.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if cred is None:
                raise ApiError("not_found", "No such passkey.")
            count = (
                await s.execute(select(func.count()).where(WebauthnCredential.user_id == user.id))
            ).scalar_one()
            fallback = user.totp_enabled and user.password_hash is not None
            if count <= 1 and not fallback:
                raise ApiError(
                    "last_credential",
                    "This is your only way in. Add another passkey, or set a password and "
                    "turn on codes first.",
                )
            await s.delete(cred)
            await self._audit(
                s,
                principal,
                "auth.passkey.removed",
                entity="webauthn_credential",
                entity_id=passkey_id,
            )

    # ------------------------------------------------------------------ TOTP + password

    async def totp_setup(self, principal: Principal) -> TotpSetupOut:
        async with self._scoped(principal) as s:
            user = await self._gate_new_credential(s, principal)
            if user.totp_enabled:
                raise ApiError("conflict", "Codes are already on. Turn them off first.")
            secret = totp.new_secret()
            user.totp_secret_enc = crypto.encrypt(secret).encode("ascii")
            await self._audit(s, principal, "auth.totp.setup")
        return TotpSetupOut(otpauth_uri=totp.provisioning_uri(secret, user.email), secret=secret)

    async def totp_enable(self, principal: Principal, code: str) -> list[str]:
        async with self._scoped(principal) as s:
            user = await self._gate_new_credential(s, principal)
            if user.totp_enabled:
                raise ApiError("conflict", "Codes are already on.")
            if not await self._totp_matches(user, code):
                raise ApiError("invalid_code", INVALID_CODE)
            user.totp_enabled = True
            codes = totp.new_recovery_codes()
            await s.execute(delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user.id))
            s.add_all(
                TotpRecoveryCode(user_id=user.id, code_hash=totp.recovery_code_hash(c))
                for c in codes
            )
            await self._audit(s, principal, "auth.totp.enabled")
        if principal.is_owner:
            await clear_setup_token(self.ctx.redis)
        return codes

    async def totp_disable(self, principal: Principal) -> None:
        require_fresh(principal)
        async with self._scoped(principal) as s:
            user = await get_user(s, principal.user_id, for_update=True)
            if not await has_passkey(s, user.id):
                raise ApiError("last_credential", "Add a passkey before turning off codes.")
            user.totp_enabled = False
            user.totp_secret_enc = None
            await s.execute(delete(TotpRecoveryCode).where(TotpRecoveryCode.user_id == user.id))
            await self._audit(s, principal, "auth.totp.disabled")

    async def set_password(self, principal: Principal, password: str) -> None:
        require_fresh(principal)
        hashed = passwords.hash_password(password)
        async with self._scoped(principal) as s:
            user = await get_user(s, principal.user_id)
            user.password_hash = hashed
            await self._audit(s, principal, "auth.password.set")
