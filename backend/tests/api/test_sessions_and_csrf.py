"""CSRF header, cookie flags, session expiry and revocation, error shapes."""

from __future__ import annotations

import hashlib
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.db_context import unscoped_session
from app.models import AuthSession
from app.services.sessions import utcnow
from tests.api.conftest import Owner, age_sessions, do_setup, make_client, session_cookie

NON_GET = [
    ("POST", "/api/setup"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/passkey/login/options"),
    ("POST", "/api/auth/password/login"),
    ("PATCH", "/api/household"),
    ("DELETE", "/api/auth/passkeys/00000000-0000-0000-0000-000000000000"),
    ("POST", "/api/does-not-exist"),
]


@pytest.mark.parametrize(("method", "path"), NON_GET)
@pytest.mark.parametrize("header", [None, "0", "true", ""])
async def test_non_get_without_csrf_header_is_rejected(
    app: FastAPI, method: str, path: str, header: str | None
) -> None:
    async with make_client(app, csrf=False) as c:
        headers = {} if header is None else {"X-Holdfast-CSRF": header}
        r = await c.request(method, path, json={}, headers=headers)
        assert r.status_code == 403
        assert r.json() == {
            "error": {"code": "csrf_failed", "message": "Missing CSRF header. Reload the page."}
        }


async def test_get_needs_no_csrf_header(app: FastAPI) -> None:
    async with make_client(app, csrf=False) as c:
        assert (await c.get("/api/setup/status")).status_code == 200


async def test_csrf_applies_even_with_a_valid_session(app: FastAPI, owner: Owner) -> None:
    async with make_client(app, csrf=False) as c:
        c.cookies.set("hf_session", session_cookie(owner.client) or "")
        r = await c.patch("/api/household", json={"name": "Cross-site"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "csrf_failed"
    assert (await owner.client.get("/api/household")).json()["name"] == "Example Household"


async def test_session_cookie_flags(client: httpx.AsyncClient, setup_token: str) -> None:
    r = await do_setup(client, setup_token)
    header = r.headers["set-cookie"]
    parts = [p.strip().lower() for p in header.split(";")]
    assert parts[0].startswith("hf_session=")
    for flag in ("httponly", "secure", "samesite=strict", "path=/", "max-age=43200"):
        assert flag in parts, flag
    token = header.split(";")[0].split("=", 1)[1]
    assert len(token) >= 43  # 32 random bytes


async def test_only_the_token_hash_is_stored(
    app: FastAPI, client: httpx.AsyncClient, setup_token: str
) -> None:
    await do_setup(client, setup_token)
    token = session_cookie(client)
    assert token
    async with unscoped_session(app.state.session_factory) as s:
        rows = (await s.execute(select(AuthSession))).scalars().all()
    assert [r.token_hash for r in rows] == [hashlib.sha256(token.encode()).digest()]
    s_row = rows[0]
    assert s_row.expires_at - s_row.created_at == timedelta(hours=12)


async def test_logout_revokes_the_session(app: FastAPI, owner: Owner) -> None:
    token = session_cookie(owner.client)
    r = await owner.client.post("/api/auth/logout")
    assert r.status_code == 204
    assert (
        'hf_session=""' in r.headers["set-cookie"] or "max-age=0" in r.headers["set-cookie"].lower()
    )
    async with make_client(app) as c:
        c.cookies.set("hf_session", token or "")
        me = await c.get("/api/auth/me")
        assert me.status_code == 401
        assert me.json()["error"]["code"] == "unauthenticated"


async def test_logout_without_session_is_still_204(app: FastAPI) -> None:
    async with make_client(app) as c:
        assert (await c.post("/api/auth/logout")).status_code == 204


async def test_expired_session_is_rejected(app: FastAPI, owner: Owner) -> None:
    await age_sessions(app, expires_at=utcnow() - timedelta(seconds=1))
    r = await owner.client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


async def test_unknown_or_missing_cookie_is_401(app: FastAPI) -> None:
    async with make_client(app) as c:
        assert (await c.get("/api/auth/me")).status_code == 401
        c.cookies.set("hf_session", "x" * 43)
        assert (await c.get("/api/household")).status_code == 401
        c.cookies.set("hf_session", "x" * 5000)
        assert (await c.get("/api/household")).status_code == 401


async def test_reauth_window_is_five_minutes(app: FastAPI, owner: Owner) -> None:
    c = owner.client
    await age_sessions(app, reauth_at=utcnow() - timedelta(minutes=4, seconds=50))
    assert (await c.get("/api/auth/me")).json()["reauth_fresh"] is True
    await age_sessions(app, reauth_at=utcnow() - timedelta(minutes=5, seconds=5))
    assert (await c.get("/api/auth/me")).json()["reauth_fresh"] is False


async def test_people_crud_needs_no_reauth(app: FastAPI, owner: Owner) -> None:
    await age_sessions(app, reauth_at=None)
    r = await owner.client.post(
        "/api/household/people", json={"display_name": "Sam Example", "filing_role": "spouse"}
    )
    assert r.status_code == 201
    assert (await owner.client.patch("/api/household", json={"name": "Renamed"})).status_code == 200


async def test_unknown_route_uses_error_shape(app: FastAPI) -> None:
    async with make_client(app) as c:
        r = await c.get("/api/nope")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


async def test_validation_error_never_echoes_input(app: FastAPI) -> None:
    async with make_client(app) as c:
        r = await c.post(
            "/api/auth/password/login",
            json={"email": "a@example.com", "password": "hunter2-secret-value"},
        )
        assert r.status_code == 422
        assert "hunter2" not in r.text
        assert set(r.json()) == {"error"}
