"""Passkeys: registration, usernameless login, sign counts, step-up, removal rules."""

from __future__ import annotations

import base64
import json

import httpx
from fastapi import FastAPI

from tests.api.conftest import (
    Owner,
    SoftAuthenticator,
    age_sessions,
    do_setup,
    enable_totp,
    make_client,
    passkey_login,
    reauth_passkey,
    register_passkey,
)


async def test_registration_options_require_discoverable_uv(
    client: httpx.AsyncClient, setup_token: str
) -> None:
    await do_setup(client, setup_token)
    options = (await client.post("/api/auth/passkey/register/options", json={})).json()
    assert options["rp"]["id"] == "localhost"
    assert options["attestation"] == "none"
    assert options["authenticatorSelection"]["residentKey"] == "required"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["user"]["name"] == "alex@example.com"
    assert len(base64.urlsafe_b64decode(options["challenge"] + "==")) >= 32


async def test_register_then_login_with_passkey(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str, authenticator: SoftAuthenticator
) -> None:
    setup = await do_setup(client, setup_token)
    reg = await register_passkey(client, authenticator, nickname="Phone")
    assert reg.status_code == 200, reg.text
    assert reg.json()["credential"]["nickname"] == "Phone"
    assert set(reg.json()["credential"]) == {"id", "nickname", "created_at"}

    async with make_client(app) as fresh:
        login = await passkey_login(fresh, authenticator)
        assert login.status_code == 200, login.text
        assert login.json()["user"]["id"] == setup.json()["user"]["id"]
        me = (await fresh.get("/api/auth/me")).json()
        assert me["reauth_fresh"] is True
        listed = (await fresh.get("/api/auth/passkeys")).json()["items"]
        assert [p["nickname"] for p in listed] == ["Phone"]
        assert listed[0]["last_used_at"] is not None
        assert set(listed[0]) == {"id", "nickname", "created_at", "last_used_at"}


async def test_registration_challenge_is_single_use_and_session_bound(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str, authenticator: SoftAuthenticator
) -> None:
    await do_setup(client, setup_token)
    options = (await client.post("/api/auth/passkey/register/options", json={})).json()
    credential = authenticator.register(options)
    # Another session can't redeem this session's challenge.
    async with make_client(app) as other:
        await do_setup(other, setup_token)  # resumes: a second session for the same owner
        stolen = await other.post(
            "/api/auth/passkey/register/verify", json={"credential": credential}
        )
        assert stolen.status_code == 400
        assert stolen.json()["error"]["code"] == "passkey_verification_failed"
    # The challenge was consumed by the failed attempt too.
    replay = await client.post("/api/auth/passkey/register/verify", json={"credential": credential})
    assert replay.status_code == 400


async def test_registration_rejects_wrong_origin_and_missing_uv(
    client: httpx.AsyncClient, setup_token: str
) -> None:
    await do_setup(client, setup_token)
    evil = SoftAuthenticator(origin="https://evil.example")
    options = (await client.post("/api/auth/passkey/register/options", json={})).json()
    r = await client.post(
        "/api/auth/passkey/register/verify", json={"credential": evil.register(options)}
    )
    assert r.json()["error"]["code"] == "passkey_verification_failed"

    no_uv = SoftAuthenticator(uv=False)
    options = (await client.post("/api/auth/passkey/register/options", json={})).json()
    r = await client.post(
        "/api/auth/passkey/register/verify", json={"credential": no_uv.register(options)}
    )
    assert r.json()["error"]["code"] == "passkey_verification_failed"


async def test_login_rejects_unknown_credential_and_replayed_challenge(
    app: FastAPI, owner: Owner
) -> None:
    stranger = SoftAuthenticator()
    stranger.register({"rp": {"id": "localhost"}, "user": {"id": "AAAA"}, "challenge": "AAAA"})
    async with make_client(app) as c:
        r = await passkey_login(c, stranger)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "passkey_verification_failed"
        assert c.cookies.get("hf_session") is None

        options = (await c.post("/api/auth/passkey/login/options", json={})).json()
        assertion = owner.authenticator.assert_(options)
        ok = await c.post("/api/auth/passkey/login/verify", json={"credential": assertion})
        assert ok.status_code == 200
        replay = await c.post("/api/auth/passkey/login/verify", json={"credential": assertion})
        assert replay.status_code == 400


async def test_login_rejects_non_increasing_sign_count(app: FastAPI, owner: Owner) -> None:
    # owner fixture already logged in once (count 1). A cloned key reporting 1 again fails.
    async with make_client(app) as c:
        r = await passkey_login(c, owner.authenticator, sign_count=1)
        assert r.status_code == 400
        ok = await passkey_login(c, owner.authenticator, sign_count=5)
        assert ok.status_code == 200


async def test_login_rejects_wrong_origin(app: FastAPI, owner: Owner) -> None:
    async with make_client(app) as c:
        r = await passkey_login(c, owner.authenticator, origin="https://evil.example")
        assert r.status_code == 400


async def test_login_rejects_mismatched_user_handle(app: FastAPI, owner: Owner) -> None:
    owner.authenticator.credentials[0].user_handle = b"\x00" * 16
    async with make_client(app) as c:
        r = await passkey_login(c, owner.authenticator)
        assert r.status_code == 400


async def test_garbage_credential_is_a_clean_400(app: FastAPI) -> None:
    async with make_client(app) as c:
        for credential in (
            {},
            {"id": "!!", "response": {}},
            {"id": "AAAA", "response": {"clientDataJSON": "bm90IGpzb24"}},
            {
                "id": "AAAA",
                "response": {
                    "clientDataJSON": base64.urlsafe_b64encode(
                        json.dumps({"challenge": 5}).encode()
                    ).decode()
                },
            },
        ):
            r = await c.post("/api/auth/passkey/login/verify", json={"credential": credential})
            assert r.status_code == 400, credential
            assert r.json()["error"]["code"] == "passkey_verification_failed"


async def test_adding_a_second_passkey_needs_fresh_reauth(app: FastAPI, owner: Owner) -> None:
    c = owner.client
    await age_sessions(app, reauth_at=None)
    stale = await c.post("/api/auth/passkey/register/options", json={})
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "reauth_required"
    await reauth_passkey(c, owner.authenticator)
    second = SoftAuthenticator()
    assert (await register_passkey(c, second, nickname="Backup")).status_code == 200
    # verify re-checks freshness too (window could close between options and verify)
    options = (await c.post("/api/auth/passkey/register/options", json={})).json()
    await age_sessions(app, reauth_at=None)
    late = await c.post(
        "/api/auth/passkey/register/verify",
        json={"credential": SoftAuthenticator().register(options)},
    )
    assert late.status_code == 403
    assert late.json()["error"]["code"] == "reauth_required"


async def test_reauth_passkey_rejects_someone_elses_key(app: FastAPI, owner: Owner) -> None:
    c = owner.client
    options = (await c.post("/api/auth/reauth/passkey/options", json={})).json()
    assert options["userVerification"] == "required"
    assert len(options["allowCredentials"]) == 1
    stranger = SoftAuthenticator()
    stranger.register({"rp": {"id": "localhost"}, "user": {"id": "AAAA"}, "challenge": "AAAA"})
    r = await c.post(
        "/api/auth/reauth/passkey/verify", json={"credential": stranger.assert_(options)}
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "passkey_verification_failed"


async def test_last_passkey_cannot_be_removed_without_fallback(app: FastAPI, owner: Owner) -> None:
    c = owner.client
    pid = (await c.get("/api/auth/passkeys")).json()["items"][0]["id"]

    await age_sessions(app, reauth_at=None)
    stale = await c.delete(f"/api/auth/passkeys/{pid}")
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "reauth_required"

    await reauth_passkey(c, owner.authenticator)
    last = await c.delete(f"/api/auth/passkeys/{pid}")
    assert last.status_code == 409
    assert last.json()["error"]["code"] == "last_credential"

    # TOTP alone isn't a way in (password login needs password + code): still refused.
    await enable_totp(c)
    assert (await c.delete(f"/api/auth/passkeys/{pid}")).status_code == 409
    assert (
        await c.post("/api/auth/password", json={"password": "correct horse battery"})
    ).status_code == 204
    assert (await c.delete(f"/api/auth/passkeys/{pid}")).status_code == 204
    assert (await c.get("/api/auth/passkeys")).json()["items"] == []


async def test_removing_a_passkey_that_isnt_mine_is_404(app: FastAPI, owner: Owner) -> None:
    import uuid

    r = await owner.client.delete(f"/api/auth/passkeys/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
