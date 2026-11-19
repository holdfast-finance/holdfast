"""Password + TOTP fallback: login, recovery codes, replay, step-up, disable rules."""

from __future__ import annotations

import time

import httpx
import pyotp
import pytest
from fastapi import FastAPI

from tests.api.conftest import (
    Owner,
    age_sessions,
    do_setup,
    enable_totp,
    make_client,
    totp_code,
)

PASSWORD = "correct horse battery staple"
EMAIL = "alex@example.com"


@pytest.fixture
async def fallback_owner(owner: Owner) -> tuple[Owner, str, list[str]]:
    secret, codes = await enable_totp(owner.client)
    r = await owner.client.post("/api/auth/password", json={"password": PASSWORD})
    assert r.status_code == 204
    return owner, secret, codes


async def _login(c: httpx.AsyncClient, **body: str) -> httpx.Response:
    return await c.post("/api/auth/password/login", json={"email": EMAIL, **body})


async def test_totp_setup_returns_uri_and_secret_only_once_enabled_with_code(
    owner: Owner,
) -> None:
    c = owner.client
    setup = (await c.post("/api/auth/totp/setup", json={})).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/Holdfast:alex%40example.com?")
    assert "issuer=Holdfast" in setup["otpauth_uri"]
    bad = await c.post("/api/auth/totp/enable", json={"code": "000000"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_code"
    ok = await c.post("/api/auth/totp/enable", json={"code": totp_code(setup["secret"])})
    codes = ok.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    me = (await c.get("/api/auth/me")).json()
    assert me["user"]["totp_enabled"] is True
    again = await c.post("/api/auth/totp/setup", json={})
    assert again.status_code == 409


async def test_password_plus_totp_login(
    app: FastAPI, fallback_owner: tuple[Owner, str, list[str]]
) -> None:
    _, secret, _ = fallback_owner
    async with make_client(app) as c:
        r = await _login(c, password=PASSWORD, totp_code=totp_code(secret, 1))
        assert r.status_code == 200, r.text
        assert r.json()["user"]["has_password"] is True
        assert (await c.get("/api/auth/me")).json()["reauth_fresh"] is True


async def test_totp_code_cannot_be_replayed(
    app: FastAPI, fallback_owner: tuple[Owner, str, list[str]]
) -> None:
    _, secret, _ = fallback_owner
    code = totp_code(secret, -1)
    async with make_client(app) as c:
        assert (await _login(c, password=PASSWORD, totp_code=code)).status_code == 200
        again = await _login(c, password=PASSWORD, totp_code=code)
        assert again.status_code == 401
        assert again.json()["error"]["code"] == "invalid_credentials"


async def test_one_generic_error_for_every_failure(
    app: FastAPI, fallback_owner: tuple[Owner, str, list[str]]
) -> None:
    _, secret, _ = fallback_owner
    async with make_client(app) as c:
        cases = [
            {"email": "nobody@example.com", "password": PASSWORD, "totp_code": "123456"},
            {"email": EMAIL, "password": "wrong password!!", "totp_code": totp_code(secret)},
            {"email": EMAIL, "password": PASSWORD, "totp_code": "000000"},
            {"email": EMAIL, "password": PASSWORD, "recovery_code": "aaaa-bbbb-cccc-dddd"},
        ]
        bodies = []
        for body in cases:
            r = await c.post("/api/auth/password/login", json=body)
            assert r.status_code == 401
            bodies.append(r.json())
        assert all(b == bodies[0] for b in bodies)
        assert bodies[0]["error"]["code"] == "invalid_credentials"


async def test_password_without_totp_is_not_a_login(app: FastAPI, owner: Owner) -> None:
    await owner.client.post("/api/auth/password", json={"password": PASSWORD})
    async with make_client(app) as c:
        r = await _login(c, password=PASSWORD, totp_code="123456")
        assert r.status_code == 401


async def test_exactly_one_second_factor_is_required(app: FastAPI) -> None:
    async with make_client(app) as c:
        neither = await c.post(
            "/api/auth/password/login", json={"email": EMAIL, "password": PASSWORD}
        )
        assert neither.status_code == 422
        assert neither.json()["error"]["code"] == "validation_error"


async def test_recovery_code_is_single_use(
    app: FastAPI, fallback_owner: tuple[Owner, str, list[str]]
) -> None:
    _, _, codes = fallback_owner
    async with make_client(app) as c:
        # Case and separators don't matter.
        r = await _login(c, password=PASSWORD, recovery_code=codes[0].upper().replace("-", " "))
        assert r.status_code == 200, r.text
        again = await _login(c, password=PASSWORD, recovery_code=codes[0])
        assert again.status_code == 401
        assert (await _login(c, password=PASSWORD, recovery_code=codes[1])).status_code == 200


async def test_wrong_password_does_not_burn_a_recovery_code(
    app: FastAPI, fallback_owner: tuple[Owner, str, list[str]]
) -> None:
    _, _, codes = fallback_owner
    async with make_client(app) as c:
        assert (
            await _login(c, password="nope nope nope", recovery_code=codes[2])
        ).status_code == 401
        assert (await _login(c, password=PASSWORD, recovery_code=codes[2])).status_code == 200


async def test_reauth_with_totp(app: FastAPI, fallback_owner: tuple[Owner, str, list[str]]) -> None:
    owner, secret, codes = fallback_owner
    c = owner.client
    await age_sessions(app, reauth_at=None)
    assert (await c.get("/api/auth/me")).json()["reauth_fresh"] is False
    bad = await c.post("/api/auth/reauth/totp", json={"code": "000000"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_code"
    # Recovery codes are for signing in, not for step-up.
    assert (await c.post("/api/auth/reauth/totp", json={"code": codes[0]})).status_code == 400
    ok = await c.post("/api/auth/reauth/totp", json={"code": totp_code(secret, 1)})
    assert ok.status_code == 204
    assert (await c.get("/api/auth/me")).json()["reauth_fresh"] is True


async def test_totp_disable_needs_reauth_and_a_passkey(
    app: FastAPI, fallback_owner: tuple[Owner, str, list[str]]
) -> None:
    owner, _, _ = fallback_owner
    c = owner.client
    await age_sessions(app, reauth_at=None)
    stale = await c.post("/api/auth/totp/disable", json={})
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "reauth_required"
    await age_sessions(app, reauth_at=_now())
    assert (await c.post("/api/auth/totp/disable", json={})).status_code == 204
    assert (await c.get("/api/auth/me")).json()["user"]["totp_enabled"] is False


async def test_totp_disable_refused_without_passkey(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str
) -> None:
    await do_setup(client, setup_token)
    await enable_totp(client)  # first credential: no step-up needed
    await age_sessions(app, reauth_at=_now())
    r = await client.post("/api/auth/totp/disable", json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "last_credential"


async def test_adding_totp_after_a_passkey_needs_fresh_reauth(app: FastAPI, owner: Owner) -> None:
    await age_sessions(app, reauth_at=None)
    for path, body in (("/api/auth/totp/setup", {}), ("/api/auth/totp/enable", {"code": "1"})):
        r = await owner.client.post(path, json=body)
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "reauth_required"


async def test_set_password_needs_reauth_and_length(app: FastAPI, owner: Owner) -> None:
    c = owner.client
    short = await c.post("/api/auth/password", json={"password": "short"})
    assert short.status_code == 422
    assert short.json()["error"]["message"] == "Use at least 12 characters."
    await age_sessions(app, reauth_at=None)
    stale = await c.post("/api/auth/password", json={"password": PASSWORD})
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "reauth_required"


def test_totp_window_is_one_step() -> None:
    from app.security.totp import matching_step

    secret = pyotp.random_base32()
    now = time.time()
    totp = pyotp.TOTP(secret)
    assert matching_step(secret, totp.at(now - 30), at=now) is not None
    assert matching_step(secret, totp.at(now + 30), at=now) is not None
    assert matching_step(secret, totp.at(now - 90), at=now) is None
    assert matching_step(secret, "12345", at=now) is None
    assert matching_step(secret, "abcdef", at=now) is None


def _now() -> object:
    from app.services.sessions import utcnow

    return utcnow()
