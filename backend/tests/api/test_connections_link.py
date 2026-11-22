"""P4-link: the link plan, slot guard, Link token, idempotent exchange, resolve and abandon,
the coexistence rule, ``GET /connections`` and the secret canaries. A fake Plaid answers on an
``httpx.MockTransport``; the network is blocked. All data is synthetic."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db_context import scoped_session
from app.integrations.plaid.client import PlaidClient, PlaidCredentials
from app.models import (
    Account,
    AuditLog,
    BankConnection,
    ConnectorAccountLink,
    PlaidLinkSlot,
)
from app.security import crypto
from app.security.secret_token import SecretToken
from app.services import plaid_setup, plaid_slots
from app.services.plaid_slots import client_fp
from tests.api.conftest import Owner, age_sessions, make_client, seed_household
from tests.api.test_imports import member_client

CLIENT_ID = "canary-client-id-9d41"
SANDBOX_SECRET = "canary-sandbox-secret-77aa"  # synthetic
PRODUCTION_SECRET = "canary-production-secret-31cc"  # synthetic
LINK_TOKEN = "link-sandbox-canary-link-token-0001"
PUBLIC_TOKEN = "public-sandbox-canary-public-token-0001"
PUBLIC_TOKEN_2 = "public-sandbox-canary-public-token-0002"
ACCESS_TOKEN = "access-sandbox-canary-access-token-0001"
ACCESS_TOKEN_2 = "access-sandbox-canary-access-token-0002"
CANARIES = (
    CLIENT_ID,
    SANDBOX_SECRET,
    PRODUCTION_SECRET,
    PUBLIC_TOKEN,
    PUBLIC_TOKEN_2,
    ACCESS_TOKEN,
    ACCESS_TOKEN_2,
)
PHRASE_ABANDON = "I did not finish this link"
CSV_HEADER = "Example Posting Date,Example Payee,Example Net Amount,Example Running Balance\n"
PLAN = {"login_kind": "bank_card", "mode": "new"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "plaid_client_id": CLIENT_ID,
            "plaid_secret_sandbox": SecretStr(SANDBOX_SECRET),
            "plaid_secret_production": SecretStr(PRODUCTION_SECRET),
            "plaid_products": ["transactions", "liabilities", "investments"],
        }
    )


class FakePlaid:
    """Answers the five Link calls. Each public token maps to one Item."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.items = {
            PUBLIC_TOKEN: ("item-one", ACCESS_TOKEN),
            PUBLIC_TOKEN_2: ("item-two", ACCESS_TOKEN_2),
        }
        self.exchanged: set[str] = set()
        self.optional_refused = False
        self.down = False
        self.accounts_fail = False
        self.sessions: list[dict[str, Any]] = []
        self.link_get_fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("no route")
        body = json.loads(request.content)
        path = request.url.path
        if path == "/link/token/create":
            if self.optional_refused and "optional_products" in body:
                return self._error(400, "INVALID_REQUEST", "INVALID_FIELD")
            return httpx.Response(
                200,
                json={
                    "link_token": LINK_TOKEN,
                    "expiration": "2026-11-23T00:00:00Z",
                    "request_id": "r1",
                },
            )
        if path == "/link/token/get":
            if self.link_get_fail:
                return self._error(400, "INVALID_REQUEST", "INVALID_LINK_TOKEN")
            return httpx.Response(200, json={"link_sessions": self.sessions, "request_id": "r2"})
        if path == "/item/public_token/exchange":
            known = self.items.get(body["public_token"])
            if known is None or body["public_token"] in self.exchanged:
                return self._error(400, "INVALID_INPUT", "INVALID_PUBLIC_TOKEN")
            self.exchanged.add(body["public_token"])
            return httpx.Response(
                200, json={"access_token": known[1], "item_id": known[0], "request_id": "r3"}
            )
        if path == "/accounts/get":
            if self.accounts_fail:
                return self._error(500, "API_ERROR", "INTERNAL_SERVER_ERROR")
            return httpx.Response(
                200,
                json={
                    "accounts": [
                        {"account_id": "acct-checking-1", "name": "Checking", "mask": "0000"},
                        {"account_id": "acct-card-2", "name": "Card", "mask": "1111"},
                    ],
                    "request_id": "r4",
                },
            )
        return httpx.Response(404, json={"error_type": "API_ERROR", "error_code": "NOT_FOUND"})

    @staticmethod
    def _error(status: int, error_type: str, code: str) -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "error_type": error_type,
                "error_code": code,
                "error_message": "free text that must not be surfaced",
                "request_id": "reqfail",
            },
        )

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaid:
    plaid = FakePlaid()

    def open_client(credentials: PlaidCredentials) -> PlaidClient:
        return PlaidClient(credentials, transport=httpx.MockTransport(plaid))

    monkeypatch.setattr(plaid_setup, "open_client", open_client)
    return plaid


def wide(app: FastAPI, hid: uuid.UUID) -> Any:
    return scoped_session(app.state.session_factory, hid, None, household_wide=True)


def hid_of(owner: Owner) -> uuid.UUID:
    return uuid.UUID(owner.household["id"])


async def add_ledger(
    app: FastAPI, hid: uuid.UUID, n: int, *, env: str = "production", state: str = "linked"
) -> None:
    fp = client_fp(CLIENT_ID)
    async with wide(app, hid) as s:
        for i in range(n):
            s.add(
                PlaidLinkSlot(
                    household_id=hid,
                    client_fp=fp,
                    env=env,
                    state=state,
                    owner_count=0 if env == "production" else None,
                    item_id=None if state == "abandoned" else f"seed-{uuid.uuid4().hex}-{i}",
                    created_by_user_id=uuid.uuid4(),
                    resolved_at=datetime.now(UTC),
                )
            )


def token_body(
    owner_count: int | None = None, phrase: str | None = None, **kw: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {**PLAN, **kw}
    if owner_count is not None:
        body["owner_count"] = owner_count
        body["not_a_duplicate"] = True
    if phrase is not None:
        body["confirm_phrase"] = phrase
    return body


async def reserve(owner: Owner) -> uuid.UUID:
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["reservation_id"])


async def slot_row(app: FastAPI, hid: uuid.UUID, rid: uuid.UUID) -> PlaidLinkSlot:
    async with wide(app, hid) as s:
        row = (await s.execute(select(PlaidLinkSlot).where(PlaidLinkSlot.id == rid))).scalar_one()
        s.expunge(row)
        return row


async def count(app: FastAPI, hid: uuid.UUID, model: Any) -> int:
    async with wide(app, hid) as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def ledger(app: FastAPI, env: str = "production") -> int:
    async with app.state.session_factory() as s:
        return int(
            await s.scalar(
                text("SELECT plaid_slot_count(:f, :e)"), {"f": client_fp(CLIENT_ID), "e": env}
            )
            or 0
        )


async def exchange(owner: Owner, rid: uuid.UUID, public: str = PUBLIC_TOKEN) -> httpx.Response:
    return await owner.client.post(
        "/api/connections/plaid/exchange", json={"public_token": public, "reservation_id": str(rid)}
    )


# --------------------------------------------------------------------------- link plan


async def test_plan_is_a_dry_call_with_the_contract_products(owner: Owner, fake: FakePlaid) -> None:
    r = await owner.client.post("/api/connections/plaid/link-plan", json=PLAN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["products"] == ["transactions"]
    assert body["optional_products"] == ["liabilities"]
    assert body["days_requested"] == 730
    assert body["ledger_count"] == 0 and body["count_source"] == "owner_slot_log"
    assert body["existing_connections"] == [] and body["phrase"] is None  # sandbox: no phrase
    for kind in ("retirement_hsa", "brokerage"):
        r = await owner.client.post("/api/connections/plaid/link-plan", json={"login_kind": kind})
        assert r.json()["products"] == ["investments"] and r.json()["days_requested"] is None
        assert r.json()["optional_products"] == []
    assert fake.requests == []  # no Plaid call, no reservation
    assert await count(owner_app(owner), hid_of(owner), PlaidLinkSlot) == 0


def owner_app(owner: Owner) -> FastAPI:
    return owner.client._transport.app  # type: ignore[attr-defined,no-any-return]


async def test_production_plan_names_the_next_slot(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    await add_ledger(app, hid_of(owner), 3)
    body = (await owner.client.post("/api/connections/plaid/link-plan", json=PLAN)).json()
    assert body["ledger_count"] == 3 and body["phrase"] == "use slot 4 of 10"


@pytest.mark.parametrize("extra", ["auth", "identity", "statements", "assets", "transfer"])
async def test_an_off_allowlist_product_is_refused_with_no_override(
    owner: Owner, app: FastAPI, fake: FakePlaid, extra: str
) -> None:
    app.state.settings.plaid_products = ["transactions", extra]
    for path, body in (
        ("link-plan", PLAN),
        ("link-token", token_body(phrase="use slot 1 of 10", owner_count=0)),
    ):
        r = await owner.client.post(f"/api/connections/plaid/{path}", json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "product_not_allowed"
    assert fake.requests == []


async def test_other_days_are_refused(owner: Owner, app: FastAPI, fake: FakePlaid) -> None:
    app.state.settings.plaid_tx_days_requested = 365
    r = await owner.client.post("/api/connections/plaid/link-plan", json=PLAN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "days_not_allowed"


async def test_plan_and_token_are_owner_only(owner: Owner, app: FastAPI, fake: FakePlaid) -> None:
    async with await member_client(app, hid_of(owner)) as member:
        for path in ("link-plan", "link-token"):
            r = await member.post(f"/api/connections/plaid/{path}", json=PLAN)
            assert r.status_code == 403
    assert fake.requests == []


# --------------------------------------------------------------------------- the slot guard


@pytest.mark.parametrize("used", [7, 8, 9])
async def test_guard_needs_the_exact_phrase_and_allows_up_to_the_tenth(
    owner: Owner, app: FastAPI, fake: FakePlaid, used: int
) -> None:
    app.state.settings.plaid_env = "production"
    await add_ledger(app, hid_of(owner), used)
    url = "/api/connections/plaid/link-token"
    for body in (
        token_body(),  # nothing typed
        token_body(owner_count=used),  # no phrase
        token_body(owner_count=used, phrase=f"use slot {used} of 10"),  # off by one
        token_body(owner_count=used, phrase=f"Use slot {used + 1} of 10"),
        {**token_body(phrase=f"use slot {used + 1} of 10"), "not_a_duplicate": True},  # no count
        {
            **token_body(owner_count=used, phrase=f"use slot {used + 1} of 10"),
            "not_a_duplicate": False,
        },
    ):
        r = await owner.client.post(url, json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "confirm_required", body
    assert fake.requests == []
    ok = await owner.client.post(
        url, json=token_body(owner_count=used, phrase=f"use slot {used + 1} of 10")
    )
    assert ok.status_code == 200, ok.text
    assert fake.paths() == ["/link/token/create"]
    row = await slot_row(app, hid_of(owner), uuid.UUID(ok.json()["reservation_id"]))
    assert (row.state, row.env, row.owner_count) == ("reserved", "production", used)
    assert await ledger(app) == used + 1  # a reservation already counts


async def test_an_eleventh_is_refused_before_any_plaid_call(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    await add_ledger(app, hid_of(owner), 10)
    for body in (token_body(), token_body(owner_count=10, phrase="use slot 11 of 10")):
        r = await owner.client.post("/api/connections/plaid/link-token", json=body)
        assert r.status_code == 409 and r.json()["error"]["code"] == "no_slots_left"
    assert fake.requests == []
    assert await ledger(app) == 10


async def test_an_owner_count_above_the_ledger_wins(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    await add_ledger(app, hid_of(owner), 2)
    url = "/api/connections/plaid/link-token"
    # The owner knows of 9 Items: the phrase is for slot 10, and the ledger's "slot 3" is wrong.
    r = await owner.client.post(url, json=token_body(owner_count=9, phrase="use slot 3 of 10"))
    assert r.json()["error"]["code"] == "confirm_required"
    r = await owner.client.post(url, json=token_body(owner_count=10, phrase="use slot 11 of 10"))
    assert r.json()["error"]["code"] == "no_slots_left"
    assert fake.requests == []
    r = await owner.client.post(url, json=token_body(owner_count=9, phrase="use slot 10 of 10"))
    assert r.status_code == 200


async def test_deleted_connections_and_other_households_still_count(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    other, _, _ = await seed_household(app, "Other Example Household", "Bo Other", "bo@example.com")
    await add_ledger(app, other, 9)  # the same Plaid client, another household
    plan = (await owner.client.post("/api/connections/plaid/link-plan", json=PLAN)).json()
    assert plan["ledger_count"] == 9
    ok = await owner.client.post(
        "/api/connections/plaid/link-token",
        json=token_body(owner_count=9, phrase="use slot 10 of 10"),
    )
    assert ok.status_code == 200
    # One open reservation per client: the second household can't start another.
    with pytest.raises(IntegrityError, match="uq_plaid_link_slot_client_fp_env_reserved"):
        async with scoped_session(app.state.session_factory, other, None) as s:
            s.add(
                PlaidLinkSlot(
                    household_id=other,
                    client_fp=client_fp(CLIENT_ID),
                    env="production",
                    state="reserved",
                    owner_count=0,
                    created_by_user_id=uuid.uuid4(),
                )
            )


async def test_a_busy_client_gives_busy_and_leaves_no_row(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    other, _, _ = await seed_household(app, "Other Example Household", "Bo Other", "bo@example.com")
    async with scoped_session(app.state.session_factory, other, None) as s:
        s.add(
            PlaidLinkSlot(
                household_id=other,
                client_fp=client_fp(CLIENT_ID),
                env="sandbox",
                state="reserved",
                created_by_user_id=uuid.uuid4(),
            )
        )
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "busy"
    assert fake.requests == [] and await count(app, hid_of(owner), PlaidLinkSlot) == 0


async def test_token_needs_a_fresh_step_up(owner: Owner, app: FastAPI, fake: FakePlaid) -> None:
    await age_sessions(app, reauth_at=datetime(2020, 1, 1, tzinfo=UTC))
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.status_code == 403 and r.json()["error"]["code"] == "reauth_required"
    assert fake.requests == []


# --------------------------------------------------------------------------- the Link token


async def test_link_token_is_sealed_hf2_bound_to_its_row_and_no_webhook_is_sent(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.status_code == 200
    body = r.json()
    assert body["link_token"] == LINK_TOKEN and body["expiration"]
    rid = uuid.UUID(body["reservation_id"])
    sent = fake.bodies("/link/token/create")[0]
    assert sent["products"] == ["transactions"] and sent["optional_products"] == ["liabilities"]
    assert sent["transactions"] == {"days_requested": 730}
    assert "webhook" not in sent and "balance" not in json.dumps(sent)
    row = await slot_row(app, hid_of(owner), rid)
    assert row.state == "reserved" and row.link_token_enc is not None
    assert LINK_TOKEN.encode() not in row.link_token_enc
    assert row.link_token_enc.startswith(b"hf2.")
    ctx = crypto.make_context(hid_of(owner), "plaid_link_slot", "link_token_enc", str(rid))
    ring_key = app.state.settings.fernet_key
    assert crypto.open(row.link_token_enc, ctx, key=ring_key).reveal() == LINK_TOKEN
    # Sealed for this row only.
    moved = crypto.make_context(
        hid_of(owner), "plaid_link_slot", "link_token_enc", str(uuid.uuid4())
    )
    with pytest.raises(crypto.CryptoError):
        crypto.open(row.link_token_enc, moved, key=ring_key)


async def test_refused_optional_products_fall_back_to_transactions_only(
    owner: Owner, fake: FakePlaid
) -> None:
    fake.optional_refused = True
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.status_code == 200
    first, second = fake.bodies("/link/token/create")
    assert "optional_products" in first and "optional_products" not in second
    assert second["products"] == ["transactions"]


async def test_a_plaid_failure_leaves_no_reservation_and_no_message_text(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    fake.down = True
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.status_code == 502 and r.json()["error"]["code"] == "plaid_unavailable"
    assert await count(app, hid_of(owner), PlaidLinkSlot) == 0
    fake.down = False
    assert (
        await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    ).status_code == 200


async def test_stale_reservations_become_unknown_and_still_count(
    owner: Owner, app: FastAPI, fake: FakePlaid, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = await reserve(owner)
    monkeypatch.setattr(plaid_slots, "RESERVATION_TTL_HOURS", -1)  # everything is now stale
    plan = await owner.client.post("/api/connections/plaid/link-plan", json=PLAN)
    assert plan.status_code == 200
    row = await slot_row(app, hid_of(owner), rid)
    assert row.state == "unknown" and row.link_token_enc is None and row.resolved_at is not None
    assert await ledger(app, "sandbox") == 1
    # An unknown reservation can be abandoned with the phrase, which releases its count.
    r = await owner.client.post(
        f"/api/connections/plaid/reservations/{rid}/abandon",
        json={"confirm_phrase": PHRASE_ABANDON},
    )
    assert r.status_code == 200 and r.json()["state"] == "abandoned"
    assert await ledger(app, "sandbox") == 0


# --------------------------------------------------------------------------- exchange


async def test_exchange_records_a_pending_setup_connection_and_seals_the_access_token(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    r = await exchange(owner, rid)
    assert r.status_code == 200, r.text
    conn = r.json()["connection"]
    assert (conn["status"], conn["provider"], conn["env"]) == ("pending_setup", "plaid", "sandbox")
    assert conn["pending_accounts"] == 2 and conn["accounts"] == []
    assert fake.paths() == [
        "/link/token/create",
        "/item/public_token/exchange",
        "/accounts/get",
    ]
    hid = hid_of(owner)
    row = await slot_row(app, hid, rid)
    assert (row.state, row.item_id, row.link_token_enc) == ("linked", "item-one", None)
    assert row.bank_connection_id == uuid.UUID(conn["id"])
    async with wide(app, hid) as s:
        stored = (await s.execute(select(BankConnection))).scalar_one()
        links = (await s.execute(select(ConnectorAccountLink))).scalars().all()
    assert (
        stored.access_token_enc.startswith(b"hf2.")
        and ACCESS_TOKEN.encode() not in stored.access_token_enc
    )
    ctx = crypto.make_context(hid, "bank_connection", "access_token_enc", str(stored.id))
    assert (
        crypto.open(stored.access_token_enc, ctx, key=app.state.settings.fernet_key).reveal()
        == ACCESS_TOKEN
    )
    assert stored.client_fp == client_fp(CLIENT_ID) and stored.external_id == "item-one"
    assert {link.state for link in links} == {"pending"} and len(links) == 2


async def test_exchange_retried_after_a_crash_makes_no_second_connection(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    """The crash is between the commit and /accounts/get: the Item is recorded, links absent."""
    rid = await reserve(owner)
    fake.accounts_fail = True
    first = await exchange(owner, rid)
    assert first.status_code == 200 and first.json()["connection"]["pending_accounts"] == 0
    hid = hid_of(owner)
    assert (
        await count(app, hid, BankConnection) == 1
        and await count(app, hid, ConnectorAccountLink) == 0
    )
    fake.accounts_fail = False
    # Plaid refuses a used public token, which is how a real retry looks. Same connection back.
    again = await exchange(owner, rid)
    assert again.status_code == 200, again.text
    assert again.json()["connection"]["id"] == first.json()["connection"]["id"]
    assert again.json()["connection"]["pending_accounts"] == 2
    assert await count(app, hid, BankConnection) == 1 and await count(app, hid, PlaidLinkSlot) == 1
    # And once more, when Plaid does accept the token again: still one connection.
    fake.exchanged.clear()
    third = await exchange(owner, rid)
    assert third.json()["connection"]["id"] == first.json()["connection"]["id"]
    assert (
        await count(app, hid, BankConnection) == 1
        and await count(app, hid, ConnectorAccountLink) == 2
    )


async def test_a_second_public_token_is_a_new_counted_flagged_item(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    one = (await exchange(owner, rid)).json()["connection"]
    two = await exchange(owner, rid, PUBLIC_TOKEN_2)
    assert two.status_code == 200 and two.json()["connection"]["id"] != one["id"]
    hid = hid_of(owner)
    assert await count(app, hid, BankConnection) == 2
    assert await ledger(app, "sandbox") == 2
    listed = (await owner.client.get("/api/connections")).json()["connections"]
    assert sorted(c["slot_flag"] or "" for c in listed) == ["", "second_public_token"]


async def test_exchange_is_bound_to_the_reservation_and_its_creator(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    _, _, cookie = await seed_household(
        app, "Other Example Household", "Bo Other", "bo@example.com"
    )
    async with make_client(app) as stranger:
        stranger.cookies.set("hf_session", cookie)
        r = await stranger.post(
            "/api/connections/plaid/exchange",
            json={"public_token": PUBLIC_TOKEN, "reservation_id": str(rid)},
        )
    assert r.status_code == 404
    unknown = await exchange(owner, uuid.uuid4())
    assert unknown.status_code == 404
    assert "/item/public_token/exchange" not in fake.paths()
    assert await count(app, hid_of(owner), BankConnection) == 0
    async with await member_client(app, hid_of(owner)) as member:
        r = await member.post(
            "/api/connections/plaid/exchange",
            json={"public_token": PUBLIC_TOKEN, "reservation_id": str(rid)},
        )
        assert r.status_code == 403


async def test_a_bad_public_token_creates_nothing(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    r = await exchange(owner, rid, "public-sandbox-never-issued")
    assert r.status_code == 409 and r.json()["error"]["code"] == "plaid_error"
    assert "INVALID_PUBLIC_TOKEN" in r.json()["error"]["message"]
    assert "free text" not in r.text
    assert await count(app, hid_of(owner), BankConnection) == 0
    assert (await slot_row(app, hid_of(owner), rid)).state == "reserved"


async def test_exchange_refused_when_the_plaid_client_changed(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    app.state.settings.plaid_client_id = "another-client-id"
    r = await exchange(owner, rid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert "/item/public_token/exchange" not in fake.paths()


# --------------------------------------------------------------------------- resolve and abandon


async def test_on_exit_alone_never_abandons(owner: Owner, app: FastAPI, fake: FakePlaid) -> None:
    rid = await reserve(owner)
    url = f"/api/connections/plaid/reservations/{rid}/resolve"
    for sessions in ([], [{"started_at": "t"}]):  # nothing yet, or a session still open
        fake.sessions = sessions
        r = await owner.client.post(url)
        assert r.status_code == 200 and r.json()["state"] == "reserved"
    fake.link_get_fail = True
    assert (await owner.client.post(url)).json()["state"] == "reserved"
    row = await slot_row(app, hid_of(owner), rid)
    assert row.state == "reserved" and row.link_token_enc is not None
    assert await ledger(app, "sandbox") == 1


async def test_resolve_abandons_only_when_plaid_confirms_no_item_and_nulls_the_token(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    fake.sessions = [{"finished_at": "2026-11-22T00:00:00Z", "on_exit": {}, "results": {}}]
    r = await owner.client.post(f"/api/connections/plaid/reservations/{rid}/resolve")
    assert r.json()["state"] == "abandoned"
    row = await slot_row(app, hid_of(owner), rid)
    assert row.link_token_enc is None and row.resolved_at is not None and row.item_id is None
    assert await ledger(app, "sandbox") == 0


async def test_resolve_recovers_a_lost_public_token(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    fake.sessions = [
        {"finished_at": "t", "results": {"item_add_results": [{"public_token": PUBLIC_TOKEN}]}}
    ]
    r = await owner.client.post(f"/api/connections/plaid/reservations/{rid}/resolve")
    assert r.status_code == 200 and r.json()["state"] == "linked"
    row = await slot_row(app, hid_of(owner), rid)
    assert row.link_token_enc is None and row.bank_connection_id is not None
    assert await count(app, hid_of(owner), BankConnection) == 1
    again = await owner.client.post(f"/api/connections/plaid/reservations/{rid}/resolve")
    assert again.json()["state"] == "linked"
    assert await count(app, hid_of(owner), BankConnection) == 1


async def test_abandon_needs_step_up_and_the_exact_phrase(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    url = f"/api/connections/plaid/reservations/{rid}/abandon"
    wrong = await owner.client.post(url, json={"confirm_phrase": "i did not finish this link"})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "confirm_required"
    await age_sessions(app, reauth_at=datetime(2020, 1, 1, tzinfo=UTC))
    stale = await owner.client.post(url, json={"confirm_phrase": PHRASE_ABANDON})
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "reauth_required"
    assert (await slot_row(app, hid_of(owner), rid)).state == "reserved"


async def test_abandon_refuses_to_forget_an_item_plaid_says_exists(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    fake.sessions = [{"results": {"item_add_results": [{"public_token": PUBLIC_TOKEN}]}}]
    r = await owner.client.post(
        f"/api/connections/plaid/reservations/{rid}/abandon",
        json={"confirm_phrase": PHRASE_ABANDON},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    assert (await slot_row(app, hid_of(owner), rid)).state == "reserved"


async def test_abandon_ends_the_open_reservation_and_clears_its_token(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    r = await owner.client.post(
        f"/api/connections/plaid/reservations/{rid}/abandon",
        json={"confirm_phrase": PHRASE_ABANDON},
    )
    assert r.status_code == 200 and r.json()["state"] == "abandoned"
    row = await slot_row(app, hid_of(owner), rid)
    assert row.link_token_enc is None
    again = await owner.client.post(
        f"/api/connections/plaid/reservations/{rid}/abandon",
        json={"confirm_phrase": PHRASE_ABANDON},
    )
    assert again.status_code == 409
    assert (await reserve(owner)) != rid  # the lock is free again


async def abandon(owner: Owner, rid: uuid.UUID) -> httpx.Response:
    return await owner.client.post(
        f"/api/connections/plaid/reservations/{rid}/abandon",
        json={"confirm_phrase": PHRASE_ABANDON},
    )


@pytest.mark.parametrize("failure", ["unreachable", "refused"])
async def test_abandon_of_a_young_reservation_is_refused_while_plaid_cannot_confirm(
    owner: Owner, app: FastAPI, fake: FakePlaid, failure: str
) -> None:
    """Link may already have made an Item, so an unanswered check must not free the slot."""
    rid = await reserve(owner)
    fake.down = failure == "unreachable"
    fake.link_get_fail = failure == "refused"
    r = await abandon(owner, rid)
    assert r.status_code == 502 and r.json()["error"]["code"] == "plaid_unavailable"
    assert "Try again" in r.json()["error"]["message"]
    row = await slot_row(app, hid_of(owner), rid)
    assert row.state == "reserved" and row.link_token_enc is not None
    assert await ledger(app, "sandbox") == 1
    fake.down = fake.link_get_fail = False
    assert (await abandon(owner, rid)).json()["state"] == "abandoned"


async def test_abandon_of_an_old_reservation_proceeds_when_plaid_cannot_answer(
    owner: Owner, app: FastAPI, fake: FakePlaid, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import plaid_link

    rid = await reserve(owner)
    monkeypatch.setattr(plaid_link, "RESERVATION_TTL_HOURS", 0)  # the token is past its life
    fake.down = True
    assert (await abandon(owner, rid)).json()["state"] == "abandoned"


async def test_abandon_ignores_only_an_unreadable_token(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    from app.security.crypto import generate_key

    rid = await reserve(owner)
    app.state.settings.fernet_key = SecretStr(generate_key())  # the sealed token no longer opens
    r = await abandon(owner, rid)
    assert r.status_code == 200 and r.json()["state"] == "abandoned"
    assert "/link/token/get" not in fake.paths()


# --------------------------------------------------------------------------- coexistence


async def real_account(app: FastAPI, hid: uuid.UUID) -> None:
    async with wide(app, hid) as s:
        s.add(Account(household_id=hid, source="manual", name="Example Cash", type="depository"))


async def sandbox_connection(app: FastAPI, hid: uuid.UUID, env: str = "sandbox") -> uuid.UUID:
    cid = uuid.uuid4()
    ctx = crypto.make_context(hid, "bank_connection", "access_token_enc", str(cid))
    async with wide(app, hid) as s:
        s.add(
            BankConnection(
                id=cid,
                household_id=hid,
                provider="plaid",
                external_id=f"item-{uuid.uuid4().hex}",
                access_token_enc=crypto.seal(
                    SecretToken("access-sandbox-synthetic"), ctx, key=app.state.settings.fernet_key
                ),
                env=env,
            )
        )
    return cid


async def test_a_sandbox_link_is_refused_on_real_data(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    await real_account(app, hid_of(owner))
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "sandbox_on_real_data"
    assert fake.requests == []


async def test_a_sandbox_link_is_refused_beside_a_production_connection(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    await sandbox_connection(app, hid_of(owner), env="production")
    r = await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    assert r.json()["error"]["code"] == "sandbox_on_real_data"


async def test_a_production_link_is_refused_beside_a_sandbox_connection(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    await sandbox_connection(app, hid_of(owner))
    r = await owner.client.post(
        "/api/connections/plaid/link-token",
        json=token_body(owner_count=0, phrase="use slot 1 of 10"),
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "sandbox_connection_present"
    assert fake.requests == [] and await ledger(app) == 0


async def test_a_sandbox_token_minted_on_an_empty_household_is_refused_at_exchange_once_data_exists(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    await real_account(app, hid_of(owner))
    r = await exchange(owner, rid)
    assert r.status_code == 409 and r.json()["error"]["code"] == "sandbox_on_real_data"
    assert "/item/public_token/exchange" not in fake.paths()
    assert await count(app, hid_of(owner), BankConnection) == 0


async def test_a_sandbox_item_does_not_make_the_household_real(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    """Accounts that belong to a sandbox connection are not real data."""
    cid = await sandbox_connection(app, hid_of(owner))
    async with wide(app, hid_of(owner)) as s:
        s.add(
            Account(
                household_id=hid_of(owner),
                bank_connection_id=cid,
                external_account_id="acct-x",
                source="plaid",
                name="Sandbox Checking",
                type="depository",
            )
        )
    assert (
        await owner.client.post("/api/connections/plaid/link-token", json=PLAN)
    ).status_code == 200


async def test_statement_and_csv_uploads_are_refused_beside_a_sandbox_connection(
    owner: Owner, app: FastAPI, fake: FakePlaid, tmp_path: Path
) -> None:
    app.state.settings.document_store = tmp_path / "docs"
    await sandbox_connection(app, hid_of(owner))
    csv = (CSV_HEADER + "2026-03-01,Example Deposit,100.00,100.00\n").encode()
    r = await owner.client.post(
        "/api/imports/statements",
        files={"file": ("export.csv", csv, "text/csv")},
        data={"account": "new", "account_kind": "bank", "joint": "true"},
    )
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "sandbox_connection_present"


# --------------------------------------------------------------------------- update mode


async def test_update_mode_reserves_nothing_and_sends_the_stored_access_token(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    conn = (await exchange(owner, rid)).json()["connection"]
    before = await count(app, hid_of(owner), PlaidLinkSlot)
    r = await owner.client.post(
        "/api/connections/plaid/link-token",
        json={"login_kind": "bank_card", "mode": "update", "connection_id": conn["id"]},
    )
    assert r.status_code == 200 and r.json()["reservation_id"] is None
    sent = fake.bodies("/link/token/create")[-1]
    assert sent["access_token"] == ACCESS_TOKEN and "products" not in sent and "webhook" not in sent
    assert await count(app, hid_of(owner), PlaidLinkSlot) == before
    # Without a reservation there is nothing to exchange: no connection can come of it.
    bad = await exchange(owner, uuid.uuid4(), PUBLIC_TOKEN_2)
    assert bad.status_code == 404 and await count(app, hid_of(owner), BankConnection) == 1


async def test_update_mode_refuses_another_client_and_an_unknown_connection(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    conn = (await exchange(owner, rid)).json()["connection"]
    missing = await owner.client.post(
        "/api/connections/plaid/link-token",
        json={"login_kind": "bank_card", "mode": "update", "connection_id": str(uuid.uuid4())},
    )
    assert missing.status_code == 404
    app.state.settings.plaid_client_id = "another-client-id"
    other = await owner.client.post(
        "/api/connections/plaid/link-token",
        json={"login_kind": "bank_card", "mode": "update", "connection_id": conn["id"]},
    )
    assert other.status_code == 409 and other.json()["error"]["code"] == "conflict"


# --------------------------------------------------------------------------- GET /connections


async def test_connections_list_hides_names_when_every_account_is_hidden_from_the_reader(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    conn = (await exchange(owner, rid)).json()["connection"]
    hid = hid_of(owner)
    async with wide(app, hid) as s:
        owner_person = (
            await s.execute(text("SELECT person_id FROM app_user WHERE role = 'owner'"))
        ).scalar_one()
        s.add(
            Account(
                household_id=hid,
                bank_connection_id=uuid.UUID(conn["id"]),
                external_account_id="acct-private",
                source="plaid",
                name="Private Example Checking",
                mask="4321",
                type="depository",
                owner_person_id=owner_person,
                visibility="private",
            )
        )
    mine = (await owner.client.get("/api/connections")).json()
    assert mine["connections"][0]["accounts"][0]["name"] == "Private Example Checking"
    assert mine["open_reservations"] == []  # linked, so nothing is open
    async with await member_client(app, hid) as member:
        theirs = await member.get("/api/connections")
        assert theirs.status_code == 200
        body = theirs.json()
        assert body["connections"][0]["accounts"] == []
        assert "Private Example Checking" not in theirs.text and "4321" not in theirs.text
        assert body["open_reservations"] == []  # owner only
    async with make_client(app) as anonymous:
        assert (await anonymous.get("/api/connections")).status_code == 401


async def test_open_reservations_are_listed_for_the_owner(owner: Owner, fake: FakePlaid) -> None:
    rid = await reserve(owner)
    body = (await owner.client.get("/api/connections")).json()
    assert [r["id"] for r in body["open_reservations"]] == [str(rid)]


# --------------------------------------------------------------------------- canaries


async def test_no_token_or_key_reaches_a_response_an_audit_row_or_a_log(
    owner: Owner, app: FastAPI, fake: FakePlaid, capfd: pytest.CaptureFixture[str]
) -> None:
    texts: list[str] = []
    plan = await owner.client.post("/api/connections/plaid/link-plan", json=PLAN)
    texts.append(plan.text)
    rid = await reserve(owner)
    bad = await exchange(owner, rid, "public-sandbox-never-issued")
    texts.append(bad.text)
    ok = await exchange(owner, rid)
    texts.append(ok.text)
    texts.append((await owner.client.get("/api/connections")).text)
    texts.append((await owner.client.get("/api/plaid/setup")).text)
    fake.link_get_fail = True
    rid2 = await reserve_after_close(owner, app)
    texts.append(
        (await owner.client.post(f"/api/connections/plaid/reservations/{rid2}/resolve")).text
    )
    # The only place the Link token may appear is the link-token response itself.
    for body in texts:
        for canary in (*CANARIES, LINK_TOKEN):
            assert canary not in body
    async with wide(app, hid_of(owner)) as s:
        rows = (await s.execute(select(AuditLog))).scalars().all()
        slots = (await s.execute(select(PlaidLinkSlot))).scalars().all()
    actions = {row.action for row in rows}
    assert {"plaid.link_token", "plaid.exchange", "plaid.accounts_get"} <= actions
    dump = json.dumps([{"d": row.detail, "e": row.entity_id, "a": row.action} for row in rows])
    for canary in (*CANARIES, LINK_TOKEN, "free text"):
        assert canary not in dump
    for slot in slots:
        for canary in (*CANARIES, LINK_TOKEN):
            assert canary.encode() not in (slot.link_token_enc or b"")
    captured = capfd.readouterr()
    for canary in (*CANARIES, LINK_TOKEN):
        assert canary not in captured.out + captured.err


async def reserve_after_close(owner: Owner, app: FastAPI) -> uuid.UUID:
    return await reserve(owner)


async def test_every_plaid_call_carries_credentials_in_headers_only(
    owner: Owner, fake: FakePlaid
) -> None:
    rid = await reserve(owner)
    await exchange(owner, rid)
    for request in fake.requests:
        assert request.headers["PLAID-CLIENT-ID"] == CLIENT_ID
        assert CLIENT_ID.encode() not in request.content
        assert SANDBOX_SECRET.encode() not in request.content
