"""P5a review fixes: a malformed Plaid page is refused whole, so the cursor never advances past
a row that was dropped, and a currency the ledger cannot hold is flagged."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.integrations.base import ConnectorError
from app.integrations.plaid.client import PlaidClient, PlaidCredentials
from app.integrations.plaid.sync import fetch_sync
from app.security.secret_token import SecretToken

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "plaid"
TODAY = date(2026, 11, 22)
DEBIT = b'"transaction_id": "tx-debit",'


def page(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def serve(name: str, old: bytes, new: bytes) -> Callable[[httpx.Request], httpx.Response]:
    """Page 1 (or 2) with one byte-level edit. Other pages are served unchanged."""

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        is_second = json.loads(request.content).get("cursor") == "cursor-1"
        served = "sync_page2" if is_second else "sync_page1"
        content = page(served)
        if served == name:
            edited = content.replace(old, new)
            assert edited != content
            content = edited
        return httpx.Response(200, content=content)

    return handler


async def run(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    creds = PlaidCredentials(env="sandbox", client_id=SecretToken("cid"), secret=SecretToken("sec"))
    async with PlaidClient(creds, transport=httpx.MockTransport(handler)) as plaid:
        return await fetch_sync(plaid, SecretToken("tok"), None, today=lambda: TODAY)


async def refused(handler: Callable[[httpx.Request], httpx.Response]) -> None:
    with pytest.raises(ConnectorError) as info:
        await run(handler)
    assert info.value.code == "UNMAPPABLE_RESPONSE"


@pytest.mark.parametrize("key", ["added", "modified", "removed", "accounts"])
async def test_a_non_object_row_is_refused_not_dropped(key: str) -> None:
    await refused(
        serve("sync_page2", f'"{key}": ['.encode(), f'"{key}": ["oops"], "ignored": ['.encode())
    )


@pytest.mark.parametrize("key", ["added", "modified", "removed", "accounts"])
async def test_a_field_that_is_not_a_list_is_refused(key: str) -> None:
    await refused(
        serve("sync_page2", f'"{key}": ['.encode(), f'"{key}": {{"a": 1}}, "ignored": ['.encode())
    )


@pytest.mark.parametrize("value", [b'"yes"', b"1", b"null"])
async def test_has_more_must_be_a_bool(value: bytes) -> None:
    await refused(serve("sync_page2", b'"has_more": false', b'"has_more": ' + value))


@pytest.mark.parametrize("cursor", [b'""', b"null", b"7"])
async def test_has_more_without_a_usable_cursor_is_refused(cursor: bytes) -> None:
    await refused(serve("sync_page1", b'"next_cursor": "cursor-1"', b'"next_cursor": ' + cursor))


async def test_a_negative_limit_is_refused_not_nulled() -> None:
    await refused(serve("sync_page1", b'"limit": 5000', b'"limit": -5000'))


async def test_transaction_currency_must_match_the_account() -> None:
    await refused(serve("sync_page1", DEBIT, DEBIT + b' "iso_currency_code": "EUR",'))


async def test_unofficial_currency_is_refused() -> None:
    await refused(
        serve(
            "sync_page1",
            DEBIT,
            DEBIT + b' "iso_currency_code": null, "unofficial_currency_code": "BTC",',
        )
    )


async def test_matching_transaction_currency_is_accepted() -> None:
    result = await run(serve("sync_page1", DEBIT, DEBIT + b' "iso_currency_code": "USD",'))
    assert any(t.external_id == "tx-debit" for t in result.added)
