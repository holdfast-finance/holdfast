"""Regression tests for the Phase 1 security re-review lows 1 and 2 (docs/phases/PHASE-1.md)."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import create_session_factory
from app.db_context import scoped_session, unscoped_session
from app.models import AppUser
from app.security import crypto, passwords, totp
from app.security.secret_token import SecretToken
from app.services import auth as auth_service
from tests.api.conftest import ORIGIN, Owner, enable_totp, totp_code

ARGON2_DELAY_SECONDS = 0.25
ME_BOUND_SECONDS = 1.0
PASSWORD = "correct horse battery staple"
EMAIL = "alex@example.com"


def _client_from(app: FastAPI, ip: str, **headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 40000)),
        base_url=ORIGIN,
        headers={"X-Holdfast-CSRF": "1", **headers},
    )


@pytest.fixture
async def tiny_pool(app: FastAPI, owner: Owner) -> AsyncIterator[None]:
    """Swap the app onto a 2-connection pool with no overflow (after the owner is set up)."""
    real_engine, real_factory = app.state.engine, app.state.session_factory
    engine = create_async_engine(
        app.state.settings.sqlalchemy_url, pool_size=2, max_overflow=0, pool_timeout=10
    )
    app.state.engine, app.state.session_factory = engine, create_session_factory(engine)
    try:
        yield
    finally:
        app.state.engine, app.state.session_factory = real_engine, real_factory
        await engine.dispose()


# --------------------------------------------------------------------------- low 1: pool vs argon2


async def test_password_login_burst_doesnt_starve_the_pool(
    app: FastAPI, owner: Owner, tiny_pool: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = passwords.verify_password

    def slow(password_hash: str | None, password: str) -> bool:
        time.sleep(ARGON2_DELAY_SECONDS)  # worker thread; stands in for a slow argon2 host
        return real(password_hash, password)

    monkeypatch.setattr(passwords, "verify_password", slow)

    async def bad_login(i: int) -> int:
        # Distinct IPs and accounts, so every attempt gets past the limiter and verifies.
        async with _client_from(app, f"198.51.100.{i + 1}") as c:
            r = await c.post(
                "/api/auth/password/login",
                json={
                    "email": f"u{i}@example.com",
                    "password": "wrong-password-xx",
                    "totp_code": "000000",
                },
            )
            return r.status_code

    logins = [asyncio.create_task(bad_login(i)) for i in range(20)]
    await asyncio.sleep(0.1)  # let the burst take its connections and queue on the semaphore
    started = time.monotonic()
    me = await owner.client.get("/api/auth/me")
    elapsed = time.monotonic() - started
    in_flight = sum(not t.done() for t in logins)
    statuses = await asyncio.gather(*logins)

    assert me.status_code == 200, me.text
    assert elapsed < ME_BOUND_SECONDS, f"/api/auth/me took {elapsed:.2f}s behind the login burst"
    assert in_flight > 0, "the burst finished before /me ran, so the test proved nothing"
    assert statuses == [401] * 20


# --------------------------------------------------------------------------- low 2: real client IP


async def test_audit_log_records_the_forwarded_client_ip_from_a_trusted_proxy(
    app: FastAPI, owner: Owner
) -> None:
    tailnet_client = "100.101.102.103"
    # 172.30.10.3 is Caddy on the compose frontend network (a default trusted proxy).
    async with _client_from(app, "172.30.10.3", **{"X-Forwarded-For": tailnet_client}) as c:
        r = await c.post(
            "/api/auth/password/login",
            json={
                "email": "alex@example.com",
                "password": "wrong-password-xx",
                "totp_code": "000000",
            },
        )
    assert r.status_code == 401, r.text

    hid = uuid.UUID(owner.household["id"])
    async with scoped_session(app.state.session_factory, hid, None) as s:
        ips = (
            (
                await s.execute(
                    text(
                        "SELECT host(ip) FROM audit_log"
                        " WHERE action = 'auth.login.password' AND detail->>'outcome' = 'failure'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert ips == [tailnet_client]


# --------------------------------------------------------------------------- review follow-ups


async def _owner_with_password_and_codes(owner: Owner) -> tuple[str, list[str]]:
    r = await owner.client.post("/api/auth/password", json={"password": PASSWORD})
    assert r.status_code == 204, r.text
    return await enable_totp(owner.client)


class _WorkSpy:
    """Counts DB sessions the login service opens and argon2 hashes it computes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.sessions = 0
        self.hashes = 0
        real_session = auth_service.unscoped_session
        real_hash = passwords.hash_password_async

        def counting_session(factory: Any) -> AbstractAsyncContextManager[Any]:
            self.sessions += 1
            return real_session(factory)

        async def counting_hash(password: str) -> str:
            self.hashes += 1
            return await real_hash(password)

        monkeypatch.setattr(auth_service, "unscoped_session", counting_session)
        monkeypatch.setattr(passwords, "hash_password_async", counting_hash)
        monkeypatch.setattr(passwords, "needs_rehash", lambda _h: True)  # every hash is "old"

    def take(self) -> tuple[int, int]:
        out = (self.sessions, self.hashes)
        self.sessions = self.hashes = 0
        return out


async def _recovery_login(app: FastAPI, ip: str, password: str, code: str) -> int:
    async with _client_from(app, ip) as c:
        r = await c.post(
            "/api/auth/password/login",
            json={"email": EMAIL, "password": password, "recovery_code": code},
        )
        return r.status_code


async def test_bad_recovery_code_costs_the_same_with_a_right_or_wrong_password(
    app: FastAPI, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, codes = await _owner_with_password_and_codes(owner)
    spy = _WorkSpy(monkeypatch)

    assert await _recovery_login(app, "198.51.100.10", PASSWORD, "AAAA-BBBB-CCCC") == 401
    right_password = spy.take()
    assert await _recovery_login(app, "198.51.100.11", "wrong-password-xx", "AAAA-BBBB-CCCC") == 401
    wrong_password = spy.take()

    assert right_password == wrong_password, (right_password, wrong_password)
    assert right_password[1] == 0, "no rehash before the second factor passed"

    # A real code still works, and the rehash happens only then, after the session is minted.
    assert await _recovery_login(app, "198.51.100.12", PASSWORD, codes[0]) == 200
    assert spy.take()[1] == 1


async def test_totp_reenrolled_during_verification_fails_the_login(
    app: FastAPI, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret, _ = await _owner_with_password_and_codes(owner)
    real_verify = passwords.verify_password_async
    user_id = uuid.UUID(owner.user["id"])

    async def verify_then_reenroll(password_hash: str | None, password: str) -> bool:
        ok = await real_verify(password_hash, password)
        # Codes re-enrolled (new secret, still enabled) while argon2 ran.
        async with unscoped_session(app.state.session_factory) as s:
            new_secret = crypto.seal(SecretToken(totp.new_secret()))
            await s.execute(
                update(AppUser).where(AppUser.id == user_id).values(totp_secret_enc=new_secret)
            )
        return ok

    monkeypatch.setattr(passwords, "verify_password_async", verify_then_reenroll)
    async with _client_from(app, "198.51.100.20") as c:
        r = await c.post(
            "/api/auth/password/login",
            json={"email": EMAIL, "password": PASSWORD, "totp_code": totp_code(secret, 1)},
        )
    assert r.status_code == 401, r.text
