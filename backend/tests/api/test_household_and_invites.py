"""Household + people CRUD (owner-only), invites, and cross-household isolation via the API."""

from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.db_context import scoped_session
from app.models import Account
from app.services.sessions import utcnow
from tests.api.conftest import (
    Owner,
    SoftAuthenticator,
    age_sessions,
    make_client,
    passkey_login,
    register_passkey,
    seed_household,
)


async def _add_person(
    c: httpx.AsyncClient, name: str = "Sam Example", role: str = "spouse"
) -> dict:  # type: ignore[type-arg]
    r = await c.post("/api/household/people", json={"display_name": name, "filing_role": role})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_household_with_two_people(owner: Owner) -> None:
    c = owner.client
    sam = await _add_person(c)
    assert sam == {
        **sam,
        "display_name": "Sam Example",
        "filing_role": "spouse",
        "has_account": False,
    }
    people = (await c.get("/api/household/people")).json()["items"]
    assert [(p["display_name"], p["has_account"]) for p in people] == [
        ("Alex Example", True),
        ("Sam Example", False),
    ]
    renamed = await c.patch("/api/household", json={"name": "  The Example   House "})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "The Example House"
    assert (await c.get("/api/household")).json()["name"] == "The Example House"


async def test_person_update_and_delete(owner: Owner) -> None:
    c = owner.client
    sam = await _add_person(c)
    r = await c.patch(f"/api/household/people/{sam['id']}", json={"filing_role": "dependent"})
    assert r.status_code == 200 and r.json()["filing_role"] == "dependent"
    bad = await c.patch(f"/api/household/people/{sam['id']}", json={"filing_role": "boss"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    empty = await c.patch(f"/api/household/people/{sam['id']}", json={})
    assert empty.status_code == 422
    assert (await c.delete(f"/api/household/people/{sam['id']}")).status_code == 204
    gone = await c.delete(f"/api/household/people/{sam['id']}")
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "not_found"


async def test_cannot_delete_a_person_with_an_account(owner: Owner) -> None:
    me = owner.user["person"]["id"]
    r = await owner.client.delete(f"/api/household/people/{me}")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


async def test_cannot_delete_a_person_who_owns_accounts(app: FastAPI, owner: Owner) -> None:
    sam = await _add_person(owner.client)
    hid = uuid.UUID(owner.household["id"])
    async with scoped_session(app.state.session_factory, hid, uuid.UUID(sam["id"])) as s:
        s.add(
            Account(
                household_id=hid,
                owner_person_id=uuid.UUID(sam["id"]),
                source="manual",
                name="Example Savings",
                type="depository",
                visibility="private",
            )
        )
    r = await owner.client.delete(f"/api/household/people/{sam['id']}")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    async with scoped_session(app.state.session_factory, hid, uuid.UUID(sam["id"])) as s:
        assert len((await s.execute(select(Account))).scalars().all()) == 1


async def test_name_limits(owner: Owner) -> None:
    c = owner.client
    long = await c.post(
        "/api/household/people", json={"display_name": "x" * 201, "filing_role": "spouse"}
    )
    assert long.status_code == 422
    assert (await c.patch("/api/household", json={"name": ""})).status_code == 422
    assert (await c.patch("/api/household", json={"name": "y" * 200})).status_code == 200


async def test_invite_create_accept_and_one_time_use(app: FastAPI, owner: Owner) -> None:
    sam = await _add_person(owner.client)
    r = await owner.client.post(
        f"/api/household/people/{sam['id']}/invite", json={"email": "Sam@Example.com"}
    )
    assert r.status_code == 200, r.text
    url = r.json()["invite_url"]
    assert url.startswith("https://localhost/invite#")
    token = url.split("#", 1)[1]
    assert len(token) >= 43

    async with make_client(app) as sam_client:
        accepted = await sam_client.post("/api/invites/accept", json={"token": token})
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body["user"]["role"] == "member"
        assert body["user"]["email"] == "sam@example.com"
        assert body["user"]["person"]["id"] == sam["id"]
        assert body["household"]["id"] == owner.household["id"]
        # First credential needs no step-up; then the member can sign in with it.
        sam_key = SoftAuthenticator()
        assert (await register_passkey(sam_client, sam_key)).status_code == 200
        login = await passkey_login(sam_client, sam_key)
        assert login.status_code == 200

        # Members can read the household but not change it.
        assert (await sam_client.get("/api/household/people")).status_code == 200
        for method, path, json_body in (
            ("PATCH", "/api/household", {"name": "Mine now"}),
            ("POST", "/api/household/people", {"display_name": "Kid", "filing_role": "dependent"}),
            ("PATCH", f"/api/household/people/{sam['id']}", {"display_name": "Sam"}),
            ("DELETE", f"/api/household/people/{owner.user['person']['id']}", None),
            ("POST", f"/api/household/people/{sam['id']}/invite", {"email": "x@example.com"}),
            ("GET", "/api/audit", None),
        ):
            resp = await sam_client.request(method, path, json=json_body)
            assert resp.status_code == 403, (method, path)
            assert resp.json()["error"]["code"] == "forbidden"

    async with make_client(app) as again:
        reused = await again.post("/api/invites/accept", json={"token": token})
        assert reused.status_code == 404
        assert reused.json()["error"]["code"] == "invalid_invite"

    people = (await owner.client.get("/api/household/people")).json()["items"]
    assert all(p["has_account"] for p in people)


async def test_invite_needs_reauth_and_rejects_conflicts(app: FastAPI, owner: Owner) -> None:
    c = owner.client
    sam = await _add_person(c)
    taken = await c.post(
        f"/api/household/people/{sam['id']}/invite", json={"email": "alex@example.com"}
    )
    assert taken.status_code == 409
    has_account = await c.post(
        f"/api/household/people/{owner.user['person']['id']}/invite",
        json={"email": "new@example.com"},
    )
    assert has_account.status_code == 409
    missing = await c.post(
        f"/api/household/people/{uuid.uuid4()}/invite", json={"email": "new@example.com"}
    )
    assert missing.status_code == 404
    await age_sessions(app, reauth_at=utcnow() - timedelta(minutes=6))
    stale = await c.post(
        f"/api/household/people/{sam['id']}/invite", json={"email": "sam@example.com"}
    )
    assert stale.status_code == 403
    assert stale.json()["error"]["code"] == "reauth_required"


async def test_new_invite_replaces_old_and_invites_expire(app: FastAPI, owner: Owner) -> None:
    c = owner.client
    sam = await _add_person(c)
    path = f"/api/household/people/{sam['id']}/invite"
    first = (await c.post(path, json={"email": "sam@example.com"})).json()["invite_url"]
    second = (await c.post(path, json={"email": "sam@example.com"})).json()["invite_url"]
    async with make_client(app) as x:
        old = await x.post("/api/invites/accept", json={"token": first.split("#")[1]})
        assert old.status_code == 404
        # Expire the live one: Redis TTL elapsed.
        redis = app.state.redis
        keys = [k async for k in redis.scan_iter("hf:invite:*")]
        assert keys
        for k in keys:
            assert 0 < await redis.ttl(k) <= 24 * 3600
            await redis.delete(k)
        expired = await x.post("/api/invites/accept", json={"token": second.split("#")[1]})
        assert expired.status_code == 404


async def test_household_isolation_through_the_api(app: FastAPI, owner: Owner) -> None:
    sam = await _add_person(owner.client)
    _, b_person, b_token = await seed_household(
        app, "Example Other Household", "Bo Other", "bo@example.com"
    )
    async with make_client(app) as b:
        b.cookies.set("hf_session", b_token)
        me = (await b.get("/api/auth/me")).json()
        assert me["household"]["name"] == "Example Other Household"
        people = (await b.get("/api/household/people")).json()["items"]
        assert [p["display_name"] for p in people] == ["Bo Other"]
        # A's ids are invisible (404), not forbidden: no existence oracle.
        for method, body in (("PATCH", {"display_name": "pwned"}), ("DELETE", None)):
            r = await b.request(method, f"/api/household/people/{sam['id']}", json=body)
            assert r.status_code == 404, method
        inv = await b.post(
            f"/api/household/people/{sam['id']}/invite", json={"email": "evil@example.com"}
        )
        assert inv.status_code == 404
        audit_items = (await b.get("/api/audit")).json()["items"]
        assert all(i["actor_display_name"] in (None, "Bo Other") for i in audit_items)
    a_people = (await owner.client.get("/api/household/people")).json()["items"]
    assert {p["display_name"] for p in a_people} == {"Alex Example", "Sam Example"}
    assert str(b_person) not in {p["id"] for p in a_people}


async def test_audit_log_page(owner: Owner) -> None:
    c = owner.client
    await _add_person(c)
    page = (await c.get("/api/audit", params={"limit": 2})).json()
    assert len(page["items"]) == 2
    assert page["next_before"]
    entry = page["items"][0]
    assert set(entry) == {"id", "created_at", "action", "actor_display_name", "ip", "outcome"}
    assert entry["action"] == "household.person.created"
    assert entry["actor_display_name"] == "Alex Example"
    assert entry["ip"] == "203.0.113.10"
    rest = (await c.get("/api/audit", params={"limit": 200, "before": page["next_before"]})).json()
    actions = [i["action"] for i in page["items"] + rest["items"]]
    assert len(set(i["id"] for i in page["items"] + rest["items"])) == len(actions)
    for expected in ("setup.complete", "auth.passkey.registered", "auth.login.passkey"):
        assert expected in actions
    assert rest["next_before"] is None
    assert (await c.get("/api/audit", params={"limit": 0})).status_code == 422
