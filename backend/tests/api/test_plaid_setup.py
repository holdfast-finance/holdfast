"""``/api/plaid/setup``: the checklist from config, the live check against a fake Plaid, and
that no key ever appears in a response, an audit row or a log line."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import structlog
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings
from app.db_context import scoped_session
from app.integrations.plaid.client import PlaidClient, PlaidCredentials
from app.models import AuditLog, BankConnection
from app.security import crypto
from app.security.secret_token import SecretToken
from app.services import plaid_setup
from tests.api.conftest import Owner, make_client, seed_household
from tests.api.test_imports import member_client

CLIENT_ID = "canary-client-id-71be"
SANDBOX_SECRET = "canary-sandbox-secret-2a90"  # synthetic
PRODUCTION_SECRET = "canary-production-secret-c44d"  # synthetic
REDIRECT = "https://money.example.com/plaid/oauth-return"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "plaid_client_id": CLIENT_ID,
            "plaid_secret_sandbox": SecretStr(SANDBOX_SECRET),
            "plaid_secret_production": SecretStr(PRODUCTION_SECRET),
            "plaid_products": ["transactions", "balance", "investments", "statements"],
        }
    )


class FakePlaid:
    """Answers like Plaid for the three things a setup check asks."""

    def __init__(
        self,
        *,
        keys_ok: bool = True,
        not_enabled: tuple[str, ...] = (),
        registered_redirects: tuple[str, ...] = (),
        down: bool = False,
    ) -> None:
        self.keys_ok = keys_ok
        self.not_enabled = not_enabled
        self.registered_redirects = registered_redirects
        self.down = down
        self.requests: list[httpx.Request] = []
        self.credentials: list[PlaidCredentials] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("no route")
        if not self.keys_ok:
            return self._error("INVALID_INPUT", "INVALID_API_KEYS")
        body = json.loads(request.content)
        if request.url.path == "/institutions/get":
            return httpx.Response(200, json={"institutions": [], "total": 0, "request_id": "ok1"})
        if request.url.path == "/link/token/create":
            if any(p in self.not_enabled for p in body["products"]):
                return self._error("INVALID_INPUT", "INVALID_PRODUCT")
            uri = body.get("redirect_uri")
            if uri is not None and uri not in self.registered_redirects:
                return self._error("INVALID_REQUEST", "INVALID_FIELD")
            return httpx.Response(
                200, json={"link_token": "link-sandbox-canary-token", "request_id": "ok2"}
            )
        return httpx.Response(404, json={"error_type": "API_ERROR", "error_code": "NOT_FOUND"})

    @staticmethod
    def _error(error_type: str, code: str) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error_type": error_type,
                "error_code": code,
                "error_message": "free text that must not be surfaced",
                "request_id": "reqfail1",
            },
        )

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def link_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == "/link/token/create"]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePlaid:
    plaid = FakePlaid()

    def open_client(credentials: PlaidCredentials) -> PlaidClient:
        plaid.credentials.append(credentials)
        return PlaidClient(credentials, transport=httpx.MockTransport(plaid))

    monkeypatch.setattr(plaid_setup, "open_client", open_client)
    return plaid


def steps(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["id"]: s for s in body["steps"]}


def assert_no_key(text: str) -> None:
    for canary in (CLIENT_ID, SANDBOX_SECRET, PRODUCTION_SECRET, "link-sandbox-canary-token"):
        assert canary not in text


async def audit_rows(app: FastAPI, hid: uuid.UUID) -> list[AuditLog]:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        rows = await s.execute(select(AuditLog).where(AuditLog.action == "plaid.setup_check"))
        return list(rows.scalars())


# --------------------------------------------------------------------------- status (no network)


async def test_status_reports_config_without_calling_plaid(owner: Owner, fake: FakePlaid) -> None:
    r = await owner.client.get("/api/plaid/setup")
    assert r.status_code == 200, r.text
    body = r.json()
    assert fake.requests == []
    assert body["env"] == "sandbox"
    assert body["ready"] is False and body["checked"] is False
    assert steps(body)["keys_present"]["state"] == "done"
    assert steps(body)["keys_valid"]["state"] == "unchecked"
    assert body["suggested_redirect_uri"] == "https://localhost/plaid/oauth-return"
    assert body["redirect_uri"] is None
    assert_no_key(r.text)


async def test_status_without_keys_asks_for_them(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_client_id = None
    body = (await owner.client.get("/api/plaid/setup")).json()
    assert steps(body)["keys_present"] == {
        "id": "keys_present",
        "state": "action",
        "reason": "client_id_missing",
        "request_id": None,
    }


async def test_production_needs_the_production_secret(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    app.state.settings.plaid_secret_production = None
    body = (await owner.client.get("/api/plaid/setup")).json()
    assert steps(body)["keys_present"]["reason"] == "secret_production_missing"
    # The sandbox secret being set does not count for production.
    r = await owner.client.post("/api/plaid/setup/check")
    assert r.status_code == 200 and fake.requests == []
    assert r.json()["ready"] is False


# --------------------------------------------------------------------------- live check


async def test_check_passes_and_probes_each_requestable_product(
    owner: Owner, fake: FakePlaid
) -> None:
    r = await owner.client.post("/api/plaid/setup/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] is True and body["checked"] is True
    assert {s["id"]: s["state"] for s in body["steps"]} == {
        "keys_present": "done",
        "keys_valid": "done",
        "products": "done",
        "redirect_uri": "optional",
        "item_slots": "optional",
    }
    assert [(p["product"], p["state"]) for p in body["products"]] == [
        ("transactions", "enabled"),
        ("balance", "implicit"),
        ("investments", "enabled"),
        ("statements", "enabled"),
    ]
    assert fake.paths() == ["/institutions/get", *["/link/token/create"] * 3]
    asked = [b["products"] for b in fake.link_bodies()]
    assert asked == [["transactions"], ["investments"], ["statements"]]  # never balance
    assert fake.link_bodies()[0]["transactions"] == {"days_requested": 730}
    assert set(fake.link_bodies()[2]["statements"]) == {"start_date", "end_date"}
    assert all(
        b["user"]["client_user_id"].startswith("holdfast-setup-") for b in fake.link_bodies()
    )
    assert_no_key(r.text)


async def test_check_uses_the_active_environments_secret_and_host(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    r = await owner.client.post("/api/plaid/setup/check")
    assert r.json()["env"] == "production"
    assert {req.url.host for req in fake.requests} == {"production.plaid.com"}
    assert {req.headers["PLAID-SECRET"] for req in fake.requests} == {PRODUCTION_SECRET}
    assert steps(r.json())["item_slots"]["state"] == "done"


async def test_rejected_keys_stop_after_one_call(owner: Owner, fake: FakePlaid) -> None:
    fake.keys_ok = False
    r = await owner.client.post("/api/plaid/setup/check")
    body = r.json()
    assert body["ready"] is False
    assert steps(body)["keys_valid"] == {
        "id": "keys_valid",
        "state": "action",
        "reason": "INVALID_API_KEYS",
        "request_id": "reqfail1",
    }
    assert fake.paths() == ["/institutions/get"]
    assert "free text" not in r.text
    assert_no_key(r.text)


async def test_a_key_with_a_stray_non_ascii_character_is_reported_not_a_500(
    owner: Owner, app: FastAPI, fake: FakePlaid, caplog: pytest.LogCaptureFixture
) -> None:
    app.state.settings.plaid_secret_sandbox = SecretStr("canary-s\u00e9cret-2a90")
    with structlog.testing.capture_logs() as events:
        r = await owner.client.post("/api/plaid/setup/check")
    assert r.status_code == 200, r.text
    assert steps(r.json())["keys_valid"] == {
        "id": "keys_valid",
        "state": "action",
        "reason": "keys_malformed",
        "request_id": None,
    }
    assert fake.requests == []
    assert (
        "\u00e9" not in json.dumps(events, default=str, ensure_ascii=False) + caplog.text + r.text
    )


@pytest.mark.parametrize("bad", ["canary-se\ncret", "canary-se\rcret", "canary se cret", "x\x00y"])
async def test_a_key_with_a_line_break_or_space_inside_is_malformed_not_unreachable(
    owner: Owner, app: FastAPI, fake: FakePlaid, bad: str
) -> None:
    app.state.settings.plaid_secret_sandbox = SecretStr(bad)
    r = await owner.client.post("/api/plaid/setup/check")
    assert r.status_code == 200, r.text
    assert steps(r.json())["keys_valid"]["reason"] == "keys_malformed"
    assert fake.requests == [] and fake.credentials == []


async def test_plaid_down_is_blocked_not_a_500(owner: Owner, fake: FakePlaid) -> None:
    fake.down = True
    r = await owner.client.post("/api/plaid/setup/check")
    assert r.status_code == 200
    assert steps(r.json())["keys_valid"]["state"] == "blocked"
    assert steps(r.json())["keys_valid"]["reason"] == "plaid_unreachable"


async def test_a_product_the_plan_lacks_is_reported_and_setup_stays_ready(
    owner: Owner, fake: FakePlaid
) -> None:
    fake.not_enabled = ("statements",)
    body = (await owner.client.post("/api/plaid/setup/check")).json()
    assert body["ready"] is True
    assert steps(body)["products"]["reason"] == "some_products_unavailable"
    by_product = {p["product"]: p for p in body["products"]}
    assert by_product["statements"] == {
        "product": "statements",
        "state": "not_enabled",
        "reason": "INVALID_PRODUCT",
    }


async def test_unregistered_redirect_uri_is_caught(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_redirect_uri = REDIRECT
    body = (await owner.client.post("/api/plaid/setup/check")).json()
    assert steps(body)["redirect_uri"]["state"] == "action"
    assert steps(body)["redirect_uri"]["reason"] == "not_registered"
    assert body["ready"] is False
    # The URI is probed once, with a product already known to be enabled.
    with_uri = [b for b in fake.link_bodies() if "redirect_uri" in b]
    assert len(with_uri) == 1 and with_uri[0]["products"] == ["transactions"]


async def test_registered_redirect_uri_passes(owner: Owner, app: FastAPI, fake: FakePlaid) -> None:
    app.state.settings.plaid_redirect_uri = REDIRECT
    fake.registered_redirects = (REDIRECT,)
    body = (await owner.client.post("/api/plaid/setup/check")).json()
    assert steps(body)["redirect_uri"]["state"] == "done"
    assert body["ready"] is True


async def test_http_redirect_uri_in_production_is_refused_without_asking_plaid(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    app.state.settings.plaid_env = "production"
    app.state.settings.plaid_redirect_uri = "http://localhost/plaid/oauth-return"
    body = (await owner.client.post("/api/plaid/setup/check")).json()
    assert steps(body)["redirect_uri"]["reason"] == "https_required"
    assert all("redirect_uri" not in b for b in fake.link_bodies())


# --------------------------------------------------------------------------- slots


async def add_items(app: FastAPI, hid: uuid.UUID, n: int, env: str = "production") -> None:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        for i in range(n):
            s.add(
                BankConnection(
                    household_id=hid,
                    provider="plaid",
                    external_id=f"item-{env}-{uuid.uuid4().hex}-{i}",
                    access_token_enc=crypto.seal(SecretToken("access-sandbox-synthetic")),
                    env=env,
                )
            )


async def test_production_slots_count_only_this_households_production_items(
    owner: Owner, app: FastAPI, fake: FakePlaid
) -> None:
    hid = uuid.UUID(owner.household["id"])
    await add_items(app, hid, 8)
    await add_items(app, hid, 3, env="sandbox")
    other, _, _ = await seed_household(app, "Other Example Household", "Bo Other", "bo@example.com")
    await add_items(app, other, 2)
    app.state.settings.plaid_env = "production"
    body = (await owner.client.get("/api/plaid/setup")).json()
    assert (body["item_slots_used"], body["item_slots_total"]) == (8, 10)
    assert steps(body)["item_slots"]["reason"] == "few_slots_left"

    await add_items(app, hid, 2)
    body = (await owner.client.post("/api/plaid/setup/check")).json()
    assert steps(body)["item_slots"] == {
        "id": "item_slots",
        "state": "blocked",
        "reason": "no_slots_left",
        "request_id": None,
    }
    assert body["ready"] is False


# --------------------------------------------------------------------------- access, audit, logs


async def test_signed_out_and_non_owner_are_refused(
    app: FastAPI, owner: Owner, fake: FakePlaid
) -> None:
    async with make_client(app) as anonymous:
        assert (await anonymous.get("/api/plaid/setup")).status_code == 401
        assert (await anonymous.post("/api/plaid/setup/check")).status_code == 401
    member = await member_client(app, uuid.UUID(owner.household["id"]))
    async with member:
        assert (await member.get("/api/plaid/setup")).status_code == 403
        assert (await member.post("/api/plaid/setup/check")).status_code == 403
    assert fake.requests == []


async def test_check_needs_the_csrf_header(app: FastAPI, owner: Owner, fake: FakePlaid) -> None:
    async with make_client(app, csrf=False) as bare:
        bare.cookies = owner.client.cookies
        r = await bare.post("/api/plaid/setup/check")
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert fake.requests == []


async def test_check_is_rate_limited_per_household(owner: Owner, fake: FakePlaid) -> None:
    for _ in range(plaid_setup.CHECKS_PER_WINDOW):
        assert (await owner.client.post("/api/plaid/setup/check")).status_code == 200
    calls = len(fake.requests)
    r = await owner.client.post("/api/plaid/setup/check")
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) > 0
    assert len(fake.requests) == calls


async def test_check_is_audited_with_states_only(
    app: FastAPI, owner: Owner, fake: FakePlaid
) -> None:
    hid = uuid.UUID(owner.household["id"])
    await owner.client.get("/api/plaid/setup")
    assert await audit_rows(app, hid) == []  # reading the checklist is not a connector call
    await owner.client.post("/api/plaid/setup/check")
    fake.keys_ok = False
    await owner.client.post("/api/plaid/setup/check")
    rows = sorted(await audit_rows(app, hid), key=lambda r: r.at)
    assert [r.detail["outcome"] for r in rows] == ["success", "failure"]
    assert rows[0].detail["env"] == "sandbox"
    assert rows[1].detail["steps"]["keys_valid"] == "action"
    assert str(rows[0].user_id) == owner.user["id"]
    assert_no_key(json.dumps([r.detail for r in rows]))


async def test_no_key_or_link_token_reaches_the_logs(
    owner: Owner, app: FastAPI, fake: FakePlaid, caplog: pytest.LogCaptureFixture
) -> None:
    app.state.settings.plaid_redirect_uri = REDIRECT
    fake.not_enabled = ("investments",)
    with structlog.testing.capture_logs() as events:
        r = await owner.client.post("/api/plaid/setup/check")
    assert r.status_code == 200
    assert_no_key(json.dumps(events, default=str) + caplog.text + r.text)
    codes = {e.get("error_code") for e in events if e["event"] == "plaid_error"}
    assert codes == {"INVALID_PRODUCT", "INVALID_FIELD"}
