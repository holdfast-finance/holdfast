"""P5a round 3: a currency that can't be checked fails closed, rows carry it, folds keep links."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest

from app.domain.connector import ConnectorTxn
from app.integrations.base import ConnectorError
from app.integrations.plaid.client import PlaidClient, PlaidCredentials
from app.integrations.plaid.sync import fetch_sync
from app.security.secret_token import SecretToken

TODAY = date(2026, 11, 22)


def account(currency: str | None) -> str:
    code = "null" if currency is None else f'"{currency}"'
    return (
        '{"account_id": "acct-chk", "name": "Synth", "mask": "0000", "type": "depository",'
        ' "subtype": "checking", "balances": {"current": 1.00, "available": 1.00,'
        f' "limit": null, "iso_currency_code": {code}}}}}'
    )


def txn(
    txn_id: str = "t1",
    *,
    iso: str | None = None,
    unofficial: str | None = None,
    pending_id: str | None = None,
) -> str:
    def quote(v: str | None) -> str:
        return "null" if v is None else f'"{v}"'

    return (
        f'{{"account_id": "acct-chk", "transaction_id": "{txn_id}", "amount": 3.00,'
        ' "date": "2026-11-01", "authorized_date": null, "pending": false,'
        f' "pending_transaction_id": {quote(pending_id)}, "name": "SYNTH",'
        f' "iso_currency_code": {quote(iso)}, "unofficial_currency_code": {quote(unofficial)}}}'
    )


def page(
    *,
    accounts: list[str],
    added: list[str] | None = None,
    modified: list[str] | None = None,
    cursor: str,
    more: bool,
) -> bytes:
    return (
        f'{{"accounts": [{", ".join(accounts)}], "added": [{", ".join(added or [])}],'
        f' "modified": [{", ".join(modified or [])}], "removed": [],'
        f' "next_cursor": "{cursor}", "has_more": {str(more).lower()},'
        ' "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"}'
    ).encode()


async def run(pages: dict[str | None, bytes], start: str | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pages[json.loads(request.content).get("cursor")])

    creds = PlaidCredentials(env="sandbox", client_id=SecretToken("c"), secret=SecretToken("s"))
    async with PlaidClient(creds, transport=httpx.MockTransport(handler)) as plaid:
        return await fetch_sync(plaid, SecretToken("tok"), start, today=lambda: TODAY)


async def refused(pages: dict[str | None, bytes]) -> None:
    with pytest.raises(ConnectorError) as info:
        await run(pages)
    assert info.value.code == "UNMAPPABLE_RESPONSE"


# ------------------------------------------------------------------ unverifiable currency


@pytest.mark.parametrize("code", [{"iso": "USD"}, {"unofficial": "BTC"}])
async def test_a_currency_row_on_an_account_missing_from_the_pages_is_refused(
    code: dict[str, str],
) -> None:
    await refused({None: page(accounts=[], added=[txn(**code)], cursor="c1", more=False)})


@pytest.mark.parametrize("code", [{"iso": "USD"}, {"unofficial": "BTC"}])
async def test_a_currency_row_on_an_account_with_no_currency_is_refused(
    code: dict[str, str],
) -> None:
    pages = {None: page(accounts=[account(None)], added=[txn(**code)], cursor="c1", more=False)}
    await refused(pages)


async def test_an_account_seen_on_another_page_can_vouch_for_the_row() -> None:
    pages = {
        None: page(accounts=[account("USD")], cursor="c1", more=True),
        "c1": page(accounts=[], added=[txn(iso="USD")], cursor="c2", more=False),
    }
    batch = await run(pages)
    assert [t.currency for t in batch.added] == ["USD"]


async def test_a_row_with_no_currency_code_is_accepted_on_any_account() -> None:
    missing = await run({None: page(accounts=[], added=[txn()], cursor="c1", more=False)})
    assert len(missing.added) == 1
    null_currency = await run(
        {None: page(accounts=[account(None)], added=[txn()], cursor="c1", more=False)}
    )
    assert len(null_currency.added) == 1


# ------------------------------------------------------------------ the row carries its currency


async def test_each_row_carries_its_iso_currency() -> None:
    pages = {
        None: page(
            accounts=[account("USD")],
            added=[txn("t1", iso="USD"), txn("t2")],
            cursor="c1",
            more=False,
        )
    }
    batch = await run(pages)
    assert {t.external_id: t.currency for t in batch.added} == {"t1": "USD", "t2": None}


def test_connector_txn_currency_is_validated() -> None:
    def make(currency: str | None) -> ConnectorTxn:
        return ConnectorTxn(
            external_account_id="a",
            external_id="t",
            posted_date=TODAY,
            authorized_date=None,
            amount=__import__("decimal").Decimal("1.00"),
            description_raw="x",
            merchant_name=None,
            pfc_primary=None,
            pfc_detailed=None,
            is_pending=False,
            pending_external_id=None,
            currency=currency,
        )

    assert make("USD").currency == "USD"
    assert make(None).currency is None
    for bad in ("usd", "US", "USDX", "access-sandbox-SECRET"):
        with pytest.raises(ValueError):
            make(bad)


# ------------------------------------------------------------------ fold keeps the pending link


async def test_a_later_row_without_a_pending_link_keeps_the_earlier_one() -> None:
    pages = {
        None: page(
            accounts=[account("USD")],
            added=[txn("posted", pending_id="pend-1")],
            cursor="c1",
            more=True,
        ),
        "c1": page(
            accounts=[],
            modified=[txn("posted", pending_id=None)],
            cursor="c2",
            more=False,
        ),
    }
    batch = await run(pages)
    (row,) = batch.added
    assert row.pending_external_id == "pend-1"


async def test_a_later_row_with_its_own_pending_link_wins() -> None:
    pages = {
        None: page(
            accounts=[account("USD")],
            added=[txn("posted", pending_id="pend-1")],
            cursor="c1",
            more=True,
        ),
        "c1": page(
            accounts=[], modified=[txn("posted", pending_id="pend-2")], cursor="c2", more=False
        ),
    }
    (row,) = (await run(pages)).added
    assert row.pending_external_id == "pend-2"
