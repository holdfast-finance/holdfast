"""P5a round 2: pages fold by transaction id, a stuck cursor fails, and a null date falls back."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.integrations.base import ConnectorError
from app.integrations.plaid.client import PlaidClient, PlaidCredentials
from app.integrations.plaid.sync import fetch_sync
from app.security.secret_token import SecretToken

TODAY = date(2026, 11, 22)


def row(
    txn_id: str, amount: str, *, date_: str | None = "2026-11-01", auth: str | None = None
) -> str:
    """One Plaid transaction as JSON text. ``amount`` is the digits as Plaid sends them."""
    date_json = "null" if date_ is None else f'"{date_}"'
    auth_json = "null" if auth is None else f'"{auth}"'
    return (
        f'{{"account_id": "acct-chk", "transaction_id": "{txn_id}", "amount": {amount},'
        f' "date": {date_json}, "authorized_date": {auth_json}, "pending": false,'
        f' "pending_transaction_id": null, "name": "SYNTH"}}'
    )


def body(
    *,
    added: list[str] | None = None,
    modified: list[str] | None = None,
    removed: list[str] | None = None,
    cursor: str,
    more: bool,
) -> bytes:
    removed_rows = [f'{{"account_id": "acct-chk", "transaction_id": "{i}"}}' for i in removed or []]
    return (
        f'{{"accounts": [], "added": [{", ".join(added or [])}],'
        f' "modified": [{", ".join(modified or [])}], "removed": [{", ".join(removed_rows)}],'
        f' "next_cursor": "{cursor}", "has_more": {str(more).lower()},'
        f' "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"}}'
    ).encode()


async def run(pages: dict[str | None, bytes], start: str | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pages[json.loads(request.content).get("cursor")])

    creds = PlaidCredentials(env="sandbox", client_id=SecretToken("c"), secret=SecretToken("s"))
    async with PlaidClient(creds, transport=httpx.MockTransport(handler)) as plaid:
        return await fetch_sync(plaid, SecretToken("tok"), start, today=lambda: TODAY)


def two(first: bytes, second: bytes) -> dict[str | None, bytes]:
    return {None: first, "c1": second}


def keys(batch: Any) -> list[tuple[str, str]]:
    rows = [*batch.added, *batch.modified]
    return [(t.external_account_id, t.external_id) for t in rows]


# ------------------------------------------------------------------ fold by id


async def test_a_later_added_beats_an_earlier_modified() -> None:
    batch = await run(
        two(
            body(modified=[row("t1", "10.00")], cursor="c1", more=True),
            body(added=[row("t1", "12.50")], cursor="c2", more=False),
        )
    )
    assert [t.amount for t in batch.added] == [Decimal("-12.50")]
    assert batch.modified == ()
    assert keys(batch) == [("acct-chk", "t1")]


async def test_a_later_modified_beats_an_earlier_added_and_stays_added() -> None:
    batch = await run(
        two(
            body(added=[row("t1", "5.00")], cursor="c1", more=True),
            body(modified=[row("t1", "6.00")], cursor="c2", more=False),
        )
    )
    assert [t.amount for t in batch.added] == [Decimal("-6.00")]
    assert batch.modified == ()


async def test_duplicates_in_one_kind_keep_the_last_copy() -> None:
    batch = await run(
        two(
            body(modified=[row("t1", "1.00")], cursor="c1", more=True),
            body(modified=[row("t1", "2.00")], cursor="c2", more=False),
        )
    )
    assert [t.amount for t in batch.modified] == [Decimal("-2.00")]
    batch = await run(
        two(
            body(added=[row("t1", "1.00")], cursor="c1", more=True),
            body(added=[row("t1", "3.00")], cursor="c2", more=False),
        )
    )
    assert [t.amount for t in batch.added] == [Decimal("-3.00")]


@pytest.mark.parametrize("kind", ["added", "modified"])
async def test_a_later_removal_drops_the_earlier_row(kind: str) -> None:
    first = body(**{kind: [row("t1", "4.00"), row("t2", "9.00")]}, cursor="c1", more=True)
    batch = await run(two(first, body(removed=["t1"], cursor="c2", more=False)))
    assert [(t.external_id) for t in [*batch.added, *batch.modified]] == ["t2"]
    assert batch.removed == (("acct-chk", "t1"),)


async def test_a_row_that_returns_after_a_removal_is_not_also_removed() -> None:
    batch = await run(
        two(
            body(removed=["t1"], cursor="c1", more=True),
            body(added=[row("t1", "4.00")], cursor="c2", more=False),
        )
    )
    assert keys(batch) == [("acct-chk", "t1")]
    assert batch.removed == ()


async def test_each_id_appears_at_most_once_in_the_batch() -> None:
    batch = await run(
        two(
            body(added=[row("t1", "1.00"), row("t2", "2.00")], cursor="c1", more=True),
            body(
                added=[row("t2", "2.50")],
                modified=[row("t1", "1.50"), row("t3", "3.00")],
                removed=["t3"],
                cursor="c2",
                more=False,
            ),
        )
    )
    seen = [*keys(batch), *batch.removed]
    assert len(seen) == len(set(seen))
    assert {t.external_id: t.amount for t in batch.added} == {
        "t1": Decimal("-1.50"),
        "t2": Decimal("-2.50"),
    }


# ------------------------------------------------------------------ stuck cursor


async def test_a_cursor_that_does_not_advance_is_a_retryable_error() -> None:
    pages = {"c0": body(added=[row("t1", "1.00")], cursor="c0", more=True)}
    with pytest.raises(ConnectorError) as info:
        await run(pages, start="c0")
    assert info.value.kind == "retryable"


async def test_a_cursor_that_loops_back_midway_is_refused_with_no_batch() -> None:
    pages = two(
        body(added=[row("t1", "1.00")], cursor="c1", more=True),
        body(added=[row("t2", "1.00")], cursor="c1", more=True),
    )
    with pytest.raises(ConnectorError) as info:
        await run(pages)
    assert info.value.kind == "retryable"


async def test_the_same_final_cursor_with_no_more_pages_is_fine() -> None:
    batch = await run({"c0": body(cursor="c0", more=False)}, start="c0")
    assert batch.next_cursor == "c0"


# ------------------------------------------------------------------ null date


def handler_for(text: str) -> Callable[[], dict[str | None, bytes]]:
    return lambda: {None: body(added=[text], cursor="c1", more=False)}


async def test_a_null_date_uses_the_authorized_date() -> None:
    batch = await run(handler_for(row("t1", "1.00", date_=None, auth="2026-10-30"))())
    assert batch.added[0].posted_date == date(2026, 10, 30)


async def test_a_row_with_no_date_at_all_is_refused() -> None:
    with pytest.raises(ConnectorError) as info:
        await run(handler_for(row("t1", "1.00", date_=None))())
    assert info.value.code == "UNMAPPABLE_RESPONSE"


async def test_a_null_amount_is_still_refused() -> None:
    with pytest.raises(ConnectorError) as info:
        await run(handler_for(row("t1", "null"))())
    assert info.value.code == "UNMAPPABLE_RESPONSE"
