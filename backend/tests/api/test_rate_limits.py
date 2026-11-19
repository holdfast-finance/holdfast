"""Rate limiting and exponential lockout (Redis), per IP and per account."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.security.ratelimit import (
    ACCOUNT_THRESHOLD,
    BASE_LOCK_SECONDS,
    IP_THRESHOLD,
    MAX_LOCK_SECONDS,
    lock_seconds,
)
from tests.api.conftest import Owner, enable_totp, make_client, passkey_login, totp_code

PASSWORD = "correct horse battery staple"


def _client_from(app: FastAPI, ip: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 40000)),
        base_url="https://localhost",
        headers={"X-Holdfast-CSRF": "1"},
    )


def test_lockout_grows_exponentially_and_caps() -> None:
    assert lock_seconds(ACCOUNT_THRESHOLD - 1, ACCOUNT_THRESHOLD) == 0
    assert lock_seconds(ACCOUNT_THRESHOLD, ACCOUNT_THRESHOLD) == BASE_LOCK_SECONDS
    assert lock_seconds(ACCOUNT_THRESHOLD + 1, ACCOUNT_THRESHOLD) == 2 * BASE_LOCK_SECONDS
    assert lock_seconds(ACCOUNT_THRESHOLD + 3, ACCOUNT_THRESHOLD) == 8 * BASE_LOCK_SECONDS
    assert lock_seconds(ACCOUNT_THRESHOLD + 50, ACCOUNT_THRESHOLD) == MAX_LOCK_SECONDS


async def test_account_lockout_even_with_the_right_password(app: FastAPI, owner: Owner) -> None:
    secret, _ = await enable_totp(owner.client)
    await owner.client.post("/api/auth/password", json={"password": PASSWORD})
    body = {"email": "alex@example.com", "password": "wrong-password-xx", "totp_code": "000000"}
    # Spread over several IPs so only the account scope trips.
    for i in range(ACCOUNT_THRESHOLD):
        async with _client_from(app, f"198.51.100.{i + 1}") as c:
            r = await c.post("/api/auth/password/login", json=body)
            assert r.status_code == 401
    async with _client_from(app, "198.51.100.99") as c:
        locked = await c.post(
            "/api/auth/password/login",
            json={**body, "password": PASSWORD, "totp_code": totp_code(secret, 1)},
        )
        assert locked.status_code == 429
        assert locked.json()["error"]["code"] == "rate_limited"
        assert "Try again in 1 minute" in locked.json()["error"]["message"]
        assert 0 < int(locked.headers["Retry-After"]) <= BASE_LOCK_SECONDS


async def test_unknown_accounts_lock_the_same_way(app: FastAPI) -> None:
    body = {"email": "ghost@example.com", "password": "whatever-whatever", "totp_code": "123456"}
    async with make_client(app) as c:
        statuses = [
            (await c.post("/api/auth/password/login", json=body)).status_code
            for _ in range(ACCOUNT_THRESHOLD + 1)
        ]
    assert statuses == [401] * ACCOUNT_THRESHOLD + [429]


async def test_ip_lockout_covers_passkey_login(app: FastAPI, owner: Owner) -> None:
    async with _client_from(app, "192.0.2.77") as c:
        for _ in range(IP_THRESHOLD):
            bogus = {"id": "AAAA", "rawId": "AAAA", "response": {"clientDataJSON": "e30"}}
            r = await c.post("/api/auth/passkey/login/verify", json={"credential": bogus})
            assert r.status_code == 400
        blocked = await passkey_login(c, owner.authenticator)
        assert blocked.status_code == 429
    # Another address is unaffected.
    async with _client_from(app, "192.0.2.78") as other:
        assert (await passkey_login(other, owner.authenticator)).status_code == 200


async def test_reauth_totp_is_rate_limited(app: FastAPI, owner: Owner) -> None:
    secret, _ = await enable_totp(owner.client)
    for _ in range(ACCOUNT_THRESHOLD):
        r = await owner.client.post("/api/auth/reauth/totp", json={"code": "000000"})
        assert r.status_code == 400
    locked = await owner.client.post("/api/auth/reauth/totp", json={"code": totp_code(secret, 1)})
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers


async def test_failed_logins_are_audited(app: FastAPI, owner: Owner) -> None:
    async with make_client(app) as c:
        await c.post(
            "/api/auth/password/login",
            json={"email": "alex@example.com", "password": "nope-nope-nope", "totp_code": "1"},
        )
    items = (await owner.client.get("/api/audit")).json()["items"]
    failures = [i for i in items if i["action"] == "auth.login.password"]
    assert failures and failures[0]["outcome"] == "failure"
