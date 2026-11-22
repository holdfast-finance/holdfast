"""Plaid client: Decimal parsing, header-only credentials, code-only errors, no key in any log."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any

import httpx
import pytest
import structlog

from app.integrations.plaid.client import (
    API_VERSION,
    PlaidClient,
    PlaidCredentials,
    PlaidError,
    PlaidUnavailable,
)
from app.security.secret_token import SecretToken

CLIENT_ID = "canary-client-id-5f3a"
SECRET = "canary-secret-9c1e"  # synthetic


def creds(env: str = "sandbox") -> PlaidCredentials:
    return PlaidCredentials(
        env=env,  # type: ignore[arg-type]
        client_id=SecretToken(CLIENT_ID),
        secret=SecretToken(SECRET),
    )


def client_for(handler: Any, env: str = "sandbox") -> PlaidClient:
    return PlaidClient(creds(env), transport=httpx.MockTransport(handler))


async def test_numbers_with_a_fraction_parse_as_decimal_never_float() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"accounts":[{"balances":{"current":110.10,"available":0.1,"limit":null}}],'
            b'"total":3,"request_id":"abc123"}',
        )

    async with client_for(handler) as plaid:
        data = await plaid.post("/accounts/get", {})
    balances = data["accounts"][0]["balances"]
    assert balances["current"] == Decimal("110.10")
    assert str(balances["current"]) == "110.10"  # the digits as sent, not a binary float
    assert balances["available"] == Decimal("0.1")
    assert type(balances["current"]) is Decimal
    assert data["total"] == 3

    def no_floats(value: object) -> None:
        assert not isinstance(value, float)
        if isinstance(value, dict):
            for v in value.values():
                no_floats(v)
        elif isinstance(value, list):
            for v in value:
                no_floats(v)

    no_floats(data)


async def test_keys_go_in_headers_and_never_in_the_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"institutions": [], "request_id": "r1"})

    async with client_for(handler, "production") as plaid:
        await plaid.institutions_get(country_codes=["US"])
    request = seen[0]
    assert str(request.url) == "https://production.plaid.com/institutions/get"
    assert request.headers["PLAID-CLIENT-ID"] == CLIENT_ID
    assert request.headers["PLAID-SECRET"] == SECRET
    assert request.headers["Plaid-Version"] == API_VERSION
    body = request.content.decode()
    assert CLIENT_ID not in body and SECRET not in body
    assert json.loads(body) == {"count": 1, "offset": 0, "country_codes": ["US"]}


async def test_sandbox_and_production_use_their_own_hosts() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={"request_id": "r"})

    for env in ("sandbox", "production"):
        async with client_for(handler, env) as plaid:
            await plaid.post("/institutions/get", {})
    assert hosts == ["sandbox.plaid.com", "production.plaid.com"]


async def test_link_token_body_only_carries_options_for_requested_products() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"link_token": "link-sandbox-x", "request_id": "r"})

    async with client_for(handler) as plaid:
        await plaid.link_token_create(
            client_user_id="u1",
            client_name="Holdfast",
            products=["transactions"],
            country_codes=["US"],
            transactions_days_requested=730,
            statements_range=("2026-01-01", "2026-03-31"),
        )
        await plaid.link_token_create(
            client_user_id="u1",
            client_name="Holdfast",
            products=["statements"],
            country_codes=["US"],
            redirect_uri="https://money.example.com/plaid/oauth-return",
            transactions_days_requested=730,
            statements_range=("2026-01-01", "2026-03-31"),
        )
    assert bodies[0]["transactions"] == {"days_requested": 730}
    assert "statements" not in bodies[0] and "redirect_uri" not in bodies[0]
    assert bodies[1]["statements"] == {"start_date": "2026-01-01", "end_date": "2026-03-31"}
    assert "transactions" not in bodies[1]
    assert bodies[1]["redirect_uri"] == "https://money.example.com/plaid/oauth-return"


async def test_plaid_error_keeps_codes_and_drops_the_free_text_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error_type": "INVALID_INPUT",
                "error_code": "INVALID_API_KEYS",
                "error_message": f"invalid client_id or secret provided: {SECRET}",
                "display_message": None,
                "request_id": "HNTDNrA8F1shFEW",
            },
        )

    async with client_for(handler) as plaid:
        with pytest.raises(PlaidError) as info:
            await plaid.institutions_get(country_codes=["US"])
    error = info.value
    assert (error.error_type, error.error_code) == ("INVALID_INPUT", "INVALID_API_KEYS")
    assert error.request_id == "HNTDNrA8F1shFEW"
    assert error.http_status == 400
    assert SECRET not in str(error) and SECRET not in repr(error)
    assert error.__cause__ is None and error.__context__ is None


async def test_odd_code_fields_are_replaced_not_passed_through() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error_type": {"x": 1}, "error_code": "bad code\nwith newline", "request_id": 7},
        )

    async with client_for(handler) as plaid:
        with pytest.raises(PlaidError) as info:
            await plaid.post("/item/get", {})
    assert info.value.error_type == "UNKNOWN"
    assert info.value.error_code == "UNKNOWN"
    assert info.value.request_id is None


@pytest.mark.parametrize(
    ("raised", "reason"),
    [
        (httpx.ConnectTimeout("t"), "timeout"),
        (httpx.ConnectError(f"boom {SECRET}"), "network"),
    ],
)
async def test_transport_failures_become_plaid_unavailable(raised: Exception, reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised

    async with client_for(handler) as plaid:
        with pytest.raises(PlaidUnavailable) as info:
            await plaid.post("/institutions/get", {})
    assert info.value.reason == reason
    assert SECRET not in str(info.value)
    # No chained httpx error: it would carry the request and its headers.
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


@pytest.mark.parametrize("content", [b"<html>502</html>", b"[1,2]", b"\xff\xfe"])
async def test_a_non_plaid_body_is_unavailable_not_a_crash(content: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=content)

    async with client_for(handler) as plaid:
        with pytest.raises(PlaidUnavailable) as info:
            await plaid.post("/institutions/get", {})
    assert info.value.reason == "bad_response"


async def test_redirects_are_not_followed() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "https://elsewhere.example/"}, content=b"")

    async with client_for(handler) as plaid:
        with pytest.raises(PlaidUnavailable):
            await plaid.post("/institutions/get", {})
    assert calls == 1


async def test_no_key_reaches_a_log_line(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/down":
            raise httpx.ConnectError("refused")
        return httpx.Response(
            400,
            json={
                "error_type": "INVALID_INPUT",
                "error_code": "INVALID_API_KEYS",
                "error_message": f"echo {SECRET} {CLIENT_ID}",
                "request_id": "req1",
            },
        )

    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        async with client_for(handler) as plaid:
            with pytest.raises(PlaidError):
                await plaid.post("/institutions/get", {})
            with pytest.raises(PlaidUnavailable):
                await plaid.post("/down", {})
    rendered = json.dumps(events, default=str) + caplog.text
    assert SECRET not in rendered and CLIENT_ID not in rendered
    assert "echo" not in rendered  # Plaid's free-text message is not logged either
    error_event = next(e for e in events if e["event"] == "plaid_error")
    assert error_event["request_id"] == "req1"
    assert error_event["error_code"] == "INVALID_API_KEYS"
    assert error_event["path"] == "/institutions/get"


def test_credentials_do_not_render_their_values() -> None:
    text = repr(creds()) + str(creds())
    assert SECRET not in text and CLIENT_ID not in text


async def test_a_key_that_cannot_go_in_a_header_fails_without_quoting_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing should be sent")

    bad = PlaidCredentials(
        env="sandbox", client_id=SecretToken(CLIENT_ID), secret=SecretToken("canary-s\u00e9cret")
    )
    async with PlaidClient(bad, transport=httpx.MockTransport(handler)) as plaid:
        with pytest.raises(PlaidUnavailable) as info:
            await plaid.post("/institutions/get", {})
    assert info.value.reason == "bad_credentials"
    assert "\u00e9" not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__ is True


@pytest.mark.parametrize("status", [204, 301, 302])
async def test_only_a_2xx_with_a_body_counts_as_success(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"link_token": "x", "request_id": "r"})

    async with client_for(handler) as plaid:
        if status == 204:
            assert (await plaid.post("/x", {}))["request_id"] == "r"
        else:
            with pytest.raises(PlaidError) as info:
                await plaid.post("/x", {})
            assert info.value.error_code == "UNKNOWN"


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
async def test_non_finite_numbers_are_refused_not_parsed_as_float(constant: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"amount":' + constant + b"}")

    async with client_for(handler) as plaid:
        with pytest.raises(PlaidUnavailable) as info:
            await plaid.post("/accounts/get", {})
    assert info.value.reason == "bad_response"
