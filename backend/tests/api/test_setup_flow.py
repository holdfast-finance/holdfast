"""First-run setup: token gate, household + owner creation, resumable until a credential exists."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.main import announce_setup_token
from tests.api.conftest import (
    SoftAuthenticator,
    do_setup,
    enable_totp,
    register_passkey,
    seed_household,
)


async def test_status_reports_needs_setup_on_empty_instance(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json() == {"needs_setup": True}


async def test_bad_token_is_rejected(client: httpx.AsyncClient, setup_token: str) -> None:
    r = await do_setup(client, "not-the-token")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "invalid_setup_token"
    assert "hf_session" not in r.cookies


async def test_no_token_minted_means_no_setup(client: httpx.AsyncClient) -> None:
    r = await do_setup(client, "anything")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "invalid_setup_token"


async def test_good_token_creates_household_person_and_owner(
    client: httpx.AsyncClient, setup_token: str
) -> None:
    r = await do_setup(client, setup_token)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["household"]["name"] == "Example Household"
    user = body["user"]
    assert user["email"] == "alex@example.com"  # stored lower-case
    assert user["role"] == "owner"
    assert user["person"]["display_name"] == "Alex Example"
    assert user["person"]["filing_role"] == "primary"
    assert user["totp_enabled"] is False
    assert user["has_password"] is False
    assert set(user) == {"id", "email", "role", "person", "totp_enabled", "has_password"}
    assert r.cookies.get("hf_session")

    me = await client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["household"] == body["household"]
    # A setup token is not a credential: no step-up freshness yet.
    assert me.json()["reauth_fresh"] is False


async def test_setup_stays_resumable_until_owner_has_a_credential(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str, authenticator: SoftAuthenticator
) -> None:
    first = await do_setup(client, setup_token)
    assert first.status_code == 201
    assert (await client.get("/api/setup/status")).json() == {"needs_setup": True}

    # Browser closed before registering a passkey: the token holder resumes, no second household.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://localhost",
        headers={"X-Holdfast-CSRF": "1"},
    ) as other:
        again = await do_setup(other, setup_token, household_name="Example Household Renamed")
        assert again.status_code == 201, again.text
        assert again.json()["household"]["id"] == first.json()["household"]["id"]
        assert again.json()["household"]["name"] == "Example Household Renamed"
        assert again.json()["user"]["id"] == first.json()["user"]["id"]
        assert (await register_passkey(other, authenticator)).status_code == 200

    assert (await client.get("/api/setup/status")).json() == {"needs_setup": False}
    third = await do_setup(client, setup_token)
    assert third.status_code == 409
    assert third.json()["error"]["code"] == "setup_complete"


async def test_totp_as_first_credential_also_completes_setup(
    client: httpx.AsyncClient, setup_token: str
) -> None:
    assert (await do_setup(client, setup_token)).status_code == 201
    await enable_totp(client)
    assert (await client.get("/api/setup/status")).json() == {"needs_setup": False}


async def test_second_attempt_after_completion_is_409_even_with_new_token(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str, authenticator: SoftAuthenticator
) -> None:
    assert (await do_setup(client, setup_token)).status_code == 201
    assert (await register_passkey(client, authenticator)).status_code == 200
    # A restart after completion mints nothing.
    assert await announce_setup_token(app) is None
    r = await do_setup(client, setup_token)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "setup_complete"


async def test_restart_while_pending_replaces_the_token(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str
) -> None:
    new_token = await announce_setup_token(app)
    assert new_token is not None and new_token != setup_token
    old = await do_setup(client, setup_token)
    assert old.status_code == 403
    assert (await do_setup(client, new_token)).status_code == 201


async def test_setup_validation_uses_contract_error_shape(
    client: httpx.AsyncClient, setup_token: str
) -> None:
    r = await do_setup(client, setup_token, email="not-an-email")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["message"] == "Enter a valid email."
    long = await do_setup(client, setup_token, household_name="x" * 201)
    assert long.status_code == 422
    assert long.json()["error"]["code"] == "validation_error"
    blank = await do_setup(client, setup_token, display_name="   ")
    assert blank.status_code == 422


async def test_existing_other_owner_means_setup_complete(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    await seed_household(app, "Example Other", "Sam Other", "sam@example.com")
    # seeded owner has no credential, but setup belongs to "exactly one pending owner"; with a
    # single credential-less owner the instance is resumable, so give it a second owner.
    await seed_household(app, "Example Third", "Kai Third", "kai@example.com")
    assert (await client.get("/api/setup/status")).json() == {"needs_setup": False}


async def test_test_hook_serves_token_only_in_test_env(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str
) -> None:
    r = await client.get("/api/test/setup-token")
    assert r.status_code == 200
    assert r.json() == {"setup_token": setup_token}


async def test_test_hook_absent_outside_test_env(settings: object) -> None:
    from app.config import Settings
    from app.main import create_app

    assert isinstance(settings, Settings)
    for env in ("dev", "prod"):
        application = create_app(settings.model_copy(update={"app_env": env}))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="https://localhost"
        ) as c:
            r = await c.get("/api/test/setup-token")
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "not_found"
        await application.state.redis.aclose()
        await application.state.engine.dispose()
