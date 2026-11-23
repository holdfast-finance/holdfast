"""P5a: /transactions/sync into SyncBatch, sign mapping per product, restart, no body logged."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog

from app.domain.connector import SyncBatch
from app.integrations.base import ConnectorError
from app.integrations.plaid import sync as sync_mod
from app.integrations.plaid.client import PlaidClient, PlaidCredentials
from app.integrations.plaid.sync import MUTATION_CODE, fetch_sync
from app.security.secret_token import SecretToken

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "plaid"
ACCESS = "access-sandbox-canary-7d41"
TODAY = date(2026, 11, 22)


def page(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> PlaidClient:
    creds = PlaidCredentials(
        env="sandbox", client_id=SecretToken("cid-canary"), secret=SecretToken("sec-canary")
    )
    return PlaidClient(creds, transport=httpx.MockTransport(handler))


def two_pages(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    name = "sync_page2" if body.get("cursor") == "cursor-1" else "sync_page1"
    return httpx.Response(200, content=page(name))


async def run(handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None) -> Any:
    async with client_for(handler) as plaid:
        return await fetch_sync(plaid, SecretToken(ACCESS), cursor, today=lambda: TODAY)


async def batch() -> SyncBatch:
    return await run(two_pages)


# ------------------------------------------------------------------ signs, one per product


async def test_depository_debit_is_negative_and_credit_positive() -> None:
    by_id = {t.external_id: t for t in (await batch()).added}
    assert by_id["tx-debit"].amount == Decimal("-42.10")  # Plaid +42.10 left the account
    assert by_id["tx-credit"].amount == Decimal("2500.00")  # Plaid -2500.00 arrived


async def test_card_purchase_is_negative_and_payment_positive() -> None:
    by_id = {t.external_id: t for t in (await batch()).added}
    assert by_id["tx-purchase"].amount == Decimal("-19.99")
    assert by_id["tx-payment"].amount == Decimal("300.00")


async def test_loan_and_card_balances_are_negative_when_owed() -> None:
    balances = {b.external_account_id: b for b in (await batch()).balances}
    assert balances["acct-loan"].current == Decimal("-15000.10")
    assert balances["acct-card"].current == Decimal("-410.25")
    assert balances["acct-card"].limit == Decimal("5000.00")
    # Holdfast invariant for a card: available == limit + current.
    assert balances["acct-card"].available == Decimal("4589.75")
    assert balances["acct-chk"].current == Decimal("1250.50")  # an asset keeps its sign
    assert balances["acct-chk"].as_of == TODAY


async def test_modified_amount_is_negated_once() -> None:
    (modified,) = (await batch()).modified
    assert modified.amount == Decimal("-20.49")


async def test_zero_amount_has_no_negative_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=page("sync_page2").replace(b'"amount": 7.25', b'"amount": 0.00')
        )

    (added,) = (await run(handler)).added
    assert added.amount == Decimal("0.00")
    assert str(added.amount) == "0.00"


# ------------------------------------------------------------------ shape


async def test_pages_merge_and_history_is_complete() -> None:
    result = await batch()
    assert len(result.added) == 6
    assert result.next_cursor == "cursor-2"
    assert result.has_more is False
    assert result.history_complete is True
    assert {a.external_account_id: a.type for a in result.accounts} == {
        "acct-chk": "depository",
        "acct-card": "credit",
        "acct-loan": "loan",
    }
    assert all(not isinstance(t.amount, float) for t in result.added)


async def test_pending_posts_through_pending_transaction_id_and_removal() -> None:
    result = await batch()
    by_id = {t.external_id: t for t in result.added}
    assert by_id["tx-pending-1"].is_pending is True
    assert by_id["tx-pending-1"].pending_external_id is None
    assert by_id["tx-pending-1"].amount == Decimal("-7.00")
    assert by_id["tx-posted-1"].is_pending is False
    assert by_id["tx-posted-1"].pending_external_id == "tx-pending-1"
    assert result.removed == (("acct-chk", "tx-pending-1"),)


async def test_description_prefers_original_and_pfc_maps() -> None:
    by_id = {t.external_id: t for t in (await batch()).added}
    assert by_id["tx-debit"].description_raw == "SYNTH GROCER #12"
    assert by_id["tx-credit"].description_raw == "SYNTH PAYROLL"
    assert by_id["tx-debit"].pfc_detailed == "FOOD_AND_DRINK_GROCERIES"
    assert by_id["tx-pending-1"].pfc_primary is None
    assert by_id["tx-debit"].authorized_date == date(2026, 10, 31)


async def test_cursor_is_sent_and_omitted_when_none() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return two_pages(request)

    await run(handler, cursor=None)
    assert "cursor" not in seen[0]
    assert seen[1]["cursor"] == "cursor-1"
    seen.clear()
    await run(handler, cursor="cursor-1")
    assert seen[0]["cursor"] == "cursor-1"


async def test_history_not_complete_while_plaid_is_still_loading() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = json.loads(page("sync_page2"))
        data["transactions_update_status"] = "INITIAL_UPDATE_COMPLETE"
        return httpx.Response(200, content=json.dumps(data))

    assert (await run(handler)).history_complete is False


async def test_not_ready_keeps_the_callers_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        empty = {
            "accounts": [], "added": [], "modified": [], "removed": [],
            "next_cursor": "", "has_more": False,
            "transactions_update_status": "NOT_READY", "request_id": "r",
        }  # fmt: skip
        return httpx.Response(200, json=empty)

    result = await run(handler, cursor="keep-me")
    assert result.next_cursor == "keep-me"
    assert result.history_complete is False


async def test_page_cap_returns_has_more_with_resume_cursor() -> None:
    async with client_for(two_pages) as plaid:
        result = await fetch_sync(
            plaid, SecretToken(ACCESS), None, max_pages=1, today=lambda: TODAY
        )
    assert result.has_more is True
    assert result.next_cursor == "cursor-1"
    assert result.history_complete is False


# ------------------------------------------------------------------ mutation restart


async def test_mutation_discards_pages_and_restarts_from_the_original_cursor() -> None:
    calls: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = json.loads(request.content).get("cursor")
        calls.append(cursor)
        if cursor == "cursor-1" and calls.count("cursor-1") == 1:
            return httpx.Response(
                400,
                json={
                    "error_type": "TRANSACTIONS_ERROR",
                    "error_code": MUTATION_CODE,
                    "error_message": "x",
                    "request_id": "rq1",
                },
            )
        return two_pages(request)

    result = await run(handler)
    assert calls == [None, "cursor-1", None, "cursor-1"]  # restart from the start, not page 2
    assert len(result.added) == 6  # page 1 was not counted twice
    assert result.next_cursor == "cursor-2"


async def test_endless_mutation_gives_a_retryable_error_without_message_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error_type": "TRANSACTIONS_ERROR",
                "error_code": MUTATION_CODE,
                "error_message": f"echo {ACCESS}",
                "request_id": "rq2",
            },
        )

    with pytest.raises(ConnectorError) as info:
        await run(handler)
    assert info.value.kind == "retryable"
    assert info.value.code == MUTATION_CODE
    assert ACCESS not in str(info.value) + repr(info.value.args)


@pytest.mark.parametrize(
    ("status", "etype", "code", "kind"),
    [
        (400, "ITEM_ERROR", "ITEM_LOGIN_REQUIRED", "auth_required"),
        (429, "RATE_LIMIT_ERROR", "TRANSACTIONS_LIMIT", "rate_limited"),
        (500, "INSTITUTION_ERROR", "INSTITUTION_DOWN", "institution_down"),
        (400, "INVALID_REQUEST", "INVALID_FIELD", "invalid_request"),
        (400, "ITEM_ERROR", "PRODUCT_NOT_READY", "retryable"),
        (400, "ITEM_ERROR", "SOMETHING_NEW", "permanent"),
    ],
)
async def test_plaid_errors_classify_to_kinds(
    status: int, etype: str, code: str, kind: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status, json={"error_type": etype, "error_code": code, "request_id": "rq"}
        )

    with pytest.raises(ConnectorError) as info:
        await run(handler)
    assert (info.value.kind, info.value.code) == (kind, code)


async def test_unreachable_plaid_is_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(ConnectorError) as info:
        await run(handler)
    assert info.value.kind == "retryable"


async def test_three_decimal_amount_is_refused_not_rounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=page("sync_page2").replace(b'"amount": 7.25', b'"amount": 7.255'),
        )

    with pytest.raises(ConnectorError) as info:
        await run(handler)
    assert info.value.code == "UNMAPPABLE_RESPONSE"


async def test_float_in_a_row_is_refused() -> None:
    from app.integrations.plaid.mapping import MappingError, map_transaction

    row = json.loads(page("sync_page2"))["added"][0]
    row["amount"] = 7.25  # a float that bypassed the client's Decimal parsing
    with pytest.raises(MappingError):
        map_transaction(row)


# ------------------------------------------------------------------ nothing logged


async def test_no_plaid_body_or_token_reaches_a_log(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("cursor") == "cursor-1":
            return httpx.Response(
                400,
                json={
                    "error_type": "ITEM_ERROR",
                    "error_code": "ITEM_LOGIN_REQUIRED",
                    "error_message": f"BODY-CANARY {ACCESS}",
                    "request_id": "rq3",
                },
            )
        return httpx.Response(200, content=page("sync_page1"))

    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events, pytest.raises(ConnectorError) as info:
        await run(handler)
    rendered = json.dumps(events, default=str) + caplog.text + repr(info.value)
    for canary in (ACCESS, "BODY-CANARY", "SYNTH GROCER", "SYNTH PAYROLL", "1250.5", "sec-canary"):
        assert canary not in rendered


async def test_successful_sync_logs_no_row_data(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        await batch()
    rendered = json.dumps(events, default=str) + caplog.text
    for canary in (ACCESS, "SYNTH", "acct-chk", "42.1", "tx-debit"):
        assert canary not in rendered


def test_module_only_calls_the_sync_endpoint() -> None:
    source = Path(sync_mod.__file__).read_text()
    assert source.count("plaid.post(") == 1
    assert 'SYNC_PATH = "/transactions/sync"' in source
