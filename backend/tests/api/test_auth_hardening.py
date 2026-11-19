"""Regression tests for the Phase 1 auth security review (ADR 0010, "Hardening")."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.security import passwords, totp
from app.security.client_ip import resolve_client_ip
from app.security.ratelimit import ACCOUNT_THRESHOLD, IP_THRESHOLD
from app.services.auth import LOGIN_OPTIONS_PER_WINDOW
from app.services.invites import INVITE_UNUSABLE
from tests.api.conftest import (
    ORIGIN,
    Owner,
    enable_totp,
    make_client,
    passkey_login,
    reauth_passkey,
    register_passkey,
    seed_household,
    totp_code,
)

PASSWORD = "correct horse battery staple"


def _client_from(app: FastAPI, ip: str, **headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 40000)),
        base_url=ORIGIN,
        headers={"X-Holdfast-CSRF": "1", **headers},
    )


# --------------------------------------------------------------------------- MEDIUM: atomic limits


async def test_concurrent_totp_reauth_verifies_at_most_the_threshold(
    app: FastAPI, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    await enable_totp(owner.client)
    verified = 0
    real = totp.matching_step

    def counting(secret: str, code: str) -> int | None:
        nonlocal verified
        verified += 1
        return real(secret, code)

    monkeypatch.setattr(totp, "matching_step", counting)
    responses = await asyncio.gather(
        *(owner.client.post("/api/auth/reauth/totp", json={"code": "000000"}) for _ in range(50))
    )
    codes = [r.json()["error"]["code"] for r in responses]
    assert verified <= ACCOUNT_THRESHOLD
    assert codes.count("invalid_code") == verified
    assert codes.count("rate_limited") == 50 - verified


async def test_concurrent_password_logins_verify_at_most_the_threshold(
    app: FastAPI, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    verified = 0
    real = passwords.verify_password

    def counting(password_hash: str | None, password: str) -> bool:
        nonlocal verified
        verified += 1
        return real(password_hash, password)

    monkeypatch.setattr(passwords, "verify_password", counting)
    body = {"email": "alex@example.com", "password": "wrong-password-xx", "totp_code": "000000"}
    async with make_client(app) as c:
        responses = await asyncio.gather(
            *(c.post("/api/auth/password/login", json=body) for _ in range(50))
        )
    codes = [r.json()["error"]["code"] for r in responses]
    assert verified <= ACCOUNT_THRESHOLD
    assert codes.count("invalid_credentials") == verified
    assert codes.count("rate_limited") == 50 - verified


# --------------------------------------------------------------------------- L1: passkeys, IP scope


async def test_account_lockout_does_not_block_passkeys(app: FastAPI, owner: Owner) -> None:
    body = {"email": "alex@example.com", "password": "wrong-password-xx", "totp_code": "000000"}
    for i in range(ACCOUNT_THRESHOLD + 1):
        async with _client_from(app, f"198.51.100.{i + 1}") as c:
            await c.post("/api/auth/password/login", json=body)
    async with _client_from(app, "198.51.100.200") as c:
        locked = await c.post("/api/auth/password/login", json=body)
        assert locked.json()["error"]["code"] == "rate_limited"
        # The owner's passkey still works, for login and for step-up.
        assert (await passkey_login(c, owner.authenticator)).status_code == 200
        await reauth_passkey(c, owner.authenticator)


async def test_successful_passkey_logins_dont_use_up_the_ip_budget(
    app: FastAPI, owner: Owner
) -> None:
    async with _client_from(app, "192.0.2.50") as c:
        for _ in range(IP_THRESHOLD + 2):
            assert (await passkey_login(c, owner.authenticator)).status_code == 200


# --------------------------------------------------------------------------- L2: trusted proxies


def test_default_trusted_proxies_are_loopback_and_the_compose_subnet() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.trusted_proxies == ["127.0.0.0/8", "::1/128", "172.30.10.0/24"]


@pytest.mark.parametrize(
    "peer",
    ["198.51.100.7", "10.0.0.5", "192.168.1.20", "172.17.0.2", "172.30.11.4"],
)
def test_spoofed_forwarded_for_from_untrusted_peers_is_ignored(peer: str) -> None:
    trusted = tuple(Settings(_env_file=None).trusted_proxies)  # type: ignore[call-arg]
    assert str(resolve_client_ip(peer, "203.0.113.9", trusted)) == peer
    assert str(resolve_client_ip(peer, "127.0.0.1, 203.0.113.9", trusted)) == peer


def test_forwarded_for_from_caddy_on_the_frontend_network_is_used() -> None:
    trusted = tuple(Settings(_env_file=None).trusted_proxies)  # type: ignore[call-arg]
    assert str(resolve_client_ip("172.30.10.3", "203.0.113.9", trusted)) == "203.0.113.9"


async def test_spoofed_header_doesnt_dodge_the_ip_limit(app: FastAPI) -> None:
    body = {"email": "x@example.com", "password": "whatever-whatever", "totp_code": "1"}
    statuses = []
    for i in range(IP_THRESHOLD + 1):
        # Rotate both the spoofed client address and the account so only the IP scope counts.
        async with _client_from(app, "198.51.100.9", **{"X-Forwarded-For": f"1.2.3.{i}"}) as c:
            r = await c.post("/api/auth/password/login", json={**body, "email": f"u{i}@ex.com"})
            statuses.append(r.status_code)
    assert statuses[-1] == 429


# --------------------------------------------------------------------------- L4: invite email


async def _sam(owner: Owner) -> str:
    r = await owner.client.post(
        "/api/household/people", json={"display_name": "Sam Example", "filing_role": "spouse"}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_invite_doesnt_reveal_an_email_in_another_household(
    app: FastAPI, owner: Owner
) -> None:
    await seed_household(app, "Example Other", "Kai Other", "kai@example.com")
    sam = await _sam(owner)
    r = await owner.client.post(
        f"/api/household/people/{sam}/invite", json={"email": "kai@example.com"}
    )
    # Same answer as for a fresh email: nothing says the address exists elsewhere.
    assert r.status_code == 200, r.text
    token = r.json()["invite_url"].split("#", 1)[1]
    async with make_client(app) as c:
        accepted = await c.post("/api/invites/accept", json={"token": token})
    assert accepted.status_code == 409
    assert accepted.json()["error"] == {"code": "conflict", "message": INVITE_UNUSABLE}
    assert "Example Other" not in accepted.text and "household" not in accepted.text.lower()


async def test_invite_still_refuses_an_email_in_my_household(owner: Owner) -> None:
    sam = await _sam(owner)
    r = await owner.client.post(
        f"/api/household/people/{sam}/invite", json={"email": "alex@example.com"}
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


# --------------------------------------------------------------------------- L5: argon2 off-loop


async def test_argon2_runs_off_the_event_loop_with_bounded_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_thread = threading.get_ident()
    threads: set[int] = set()
    active = peak = 0
    lock = threading.Lock()
    real = passwords.verify_password

    def slow(password_hash: str | None, password: str) -> bool:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threads.add(threading.get_ident())
        try:
            return real(password_hash, password)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(passwords, "verify_password", slow)
    hashed = await passwords.hash_password_async(PASSWORD)
    results = await asyncio.gather(
        *(passwords.verify_password_async(hashed, PASSWORD) for _ in range(12))
    )
    assert all(results)
    assert loop_thread not in threads
    assert 1 <= peak <= passwords.MAX_CONCURRENT_HASHES


# --------------------------------------------------------------------------- L6: sizes, minting


async def test_oversized_bodies_are_refused_with_413(client: httpx.AsyncClient) -> None:
    big = b'{"email": "' + b"a" * (1024 * 1024) + b'"}'
    r = await client.post(
        "/api/auth/password/login", content=big, headers={"content-type": "application/json"}
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"

    async def chunks() -> Any:
        for _ in range(20):
            yield b"x" * 65536

    streamed = await client.post(
        "/api/auth/password/login", content=chunks(), headers={"content-type": "application/json"}
    )
    assert streamed.status_code == 413
    assert streamed.json()["error"]["code"] == "payload_too_large"


async def test_oversized_passkey_credential_is_a_validation_error(
    client: httpx.AsyncClient,
) -> None:
    credential = {"id": "AAAA", "response": {"clientDataJSON": "e" * (70 * 1024)}}
    r = await client.post("/api/auth/passkey/login/verify", json={"credential": credential})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    many = {f"k{i}": i for i in range(100)}
    r = await client.post("/api/auth/passkey/login/verify", json={"credential": many})
    assert r.status_code == 422


async def test_passkey_login_options_are_throttled_per_ip(app: FastAPI) -> None:
    async with _client_from(app, "192.0.2.123") as c:
        for _ in range(LOGIN_OPTIONS_PER_WINDOW):
            assert (await c.post("/api/auth/passkey/login/options", json={})).status_code == 200
        r = await c.post("/api/auth/passkey/login/options", json={})
        assert r.status_code == 429
        assert r.json()["error"]["code"] == "rate_limited"
        assert int(r.headers["Retry-After"]) > 0
    async with _client_from(app, "192.0.2.124") as other:
        assert (await other.post("/api/auth/passkey/login/options", json={})).status_code == 200


# --------------------------------------------------------------------------- L8: revoke sessions


async def _second_session(app: FastAPI, owner: Owner) -> httpx.AsyncClient:
    other = _client_from(app, "203.0.113.20")
    assert (await passkey_login(other, owner.authenticator)).status_code == 200
    assert (await other.get("/api/auth/me")).status_code == 200
    return other


async def _assert_only_current_survives(owner: Owner, other: httpx.AsyncClient) -> None:
    gone = await other.get("/api/auth/me")
    assert gone.status_code == 401
    assert gone.json()["error"]["code"] == "unauthenticated"
    assert (await owner.client.get("/api/auth/me")).status_code == 200
    await other.aclose()


async def test_setting_a_password_revokes_other_sessions(app: FastAPI, owner: Owner) -> None:
    other = await _second_session(app, owner)
    r = await owner.client.post("/api/auth/password", json={"password": PASSWORD})
    assert r.status_code == 204
    await _assert_only_current_survives(owner, other)


async def test_turning_totp_off_revokes_other_sessions(app: FastAPI, owner: Owner) -> None:
    await enable_totp(owner.client)
    other = await _second_session(app, owner)
    await reauth_passkey(owner.client, owner.authenticator)
    assert (await owner.client.post("/api/auth/totp/disable", json={})).status_code == 204
    await _assert_only_current_survives(owner, other)


async def test_removing_a_passkey_revokes_other_sessions(app: FastAPI, owner: Owner) -> None:
    # A second passkey, so the first can go.
    assert (await register_passkey(owner.client, owner.authenticator, "Phone")).status_code == 200
    first = owner.authenticator.credentials[0]
    items = (await owner.client.get("/api/auth/passkeys")).json()["items"]
    other = _client_from(app, "203.0.113.21")
    options = (await other.post("/api/auth/passkey/login/options", json={})).json()
    login = await other.post(
        "/api/auth/passkey/login/verify",
        json={"credential": owner.authenticator.assert_(options, credential=first)},
    )
    assert login.status_code == 200
    await reauth_passkey(owner.client, owner.authenticator)  # uses the newest passkey
    oldest = min(items, key=lambda i: i["created_at"])
    assert (await owner.client.delete(f"/api/auth/passkeys/{oldest['id']}")).status_code == 204
    await _assert_only_current_survives(owner, other)


async def test_totp_code_still_works_after_revocation(app: FastAPI, owner: Owner) -> None:
    """Sanity: revoking other sessions leaves the current one fully usable (step-up included)."""
    secret, _ = await enable_totp(owner.client)
    await owner.client.post("/api/auth/password", json={"password": PASSWORD})
    r = await owner.client.post("/api/auth/reauth/totp", json={"code": totp_code(secret, 1)})
    assert r.status_code == 204
