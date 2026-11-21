"""Read API for accounts and transactions (U1a): scoping, paging, filters, summary, money types.

All values are invented. Rows are seeded through the ORM as a household-wide job, then read
back through the real app as the signed-in people (RLS applies exactly as in production).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx
from fastapi import FastAPI

from app.db_context import scoped_session
from app.models import Account, BalanceSnapshot, Document, ImportRun, Transaction
from app.openapi_export import openapi_document
from tests.api.conftest import Owner, make_client, seed_household

WHEN = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)


def _fp(n: int) -> str:
    return f"{n:064x}"


async def _account(
    app: FastAPI,
    hid: uuid.UUID,
    name: str,
    *,
    owner_person: uuid.UUID | None = None,
    visibility: str = "shared",
    currency: str = "USD",
    mask: str | None = "4321",
) -> uuid.UUID:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        a = Account(
            household_id=hid,
            owner_person_id=owner_person,
            source="manual",
            name=name,
            type="depository",
            visibility=visibility,
            currency=currency,
            mask=mask,
        )
        s.add(a)
        await s.flush()
        return a.id


async def _tx(
    app: FastAPI,
    hid: uuid.UUID,
    account_id: uuid.UUID,
    posted: str,
    amount: str,
    description: str,
    **extra: Any,
) -> uuid.UUID:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        t = Transaction(
            household_id=hid,
            account_id=account_id,
            posted_date=date.fromisoformat(posted),
            amount=Decimal(amount),
            description_raw=description,
            source="manual",
            **extra,
        )
        s.add(t)
        await s.flush()
        return t.id


async def _person_id(owner: Owner) -> uuid.UUID:
    me = (await owner.client.get("/api/auth/me")).json()
    return uuid.UUID(me["user"]["person"]["id"])


async def test_requires_a_session(client: httpx.AsyncClient) -> None:
    for path in ("/api/accounts", "/api/transactions", "/api/transactions/summary"):
        r = await client.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "unauthenticated"


async def test_accounts_list_counts_and_balance(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    a = await _account(app, hid, "Example Checking")
    empty = await _account(app, hid, "Example Empty", mask=None)
    await _tx(app, hid, a, "2026-01-05", "-10.00", "Coffee")
    await _tx(app, hid, a, "2026-01-20", "2500.00", "Pay")
    winner = await _tx(app, hid, a, "2026-02-01", "-5.00", "Plaid copy")
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        s.add(
            BalanceSnapshot(
                account_id=a, household_id=hid, as_of_date=date(2026, 1, 1), current=Decimal("1.00")
            )
        )
        s.add(
            BalanceSnapshot(
                account_id=a,
                household_id=hid,
                as_of_date=date(2026, 1, 31),
                current=Decimal("2490.00"),
            )
        )
    # A superseded row must not count.
    loser = await _tx(app, hid, a, "2026-03-01", "-5.00", "Import copy")
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        row = await s.get(Transaction, loser)
        assert row is not None
        row.superseded_by_id = winner

    r = await owner.client.get("/api/accounts")
    assert r.status_code == 200, r.text
    items = {i["name"]: i for i in r.json()["items"]}
    chk = items["Example Checking"]
    assert chk["id"] == str(a)
    assert chk["transaction_count"] == 3
    assert chk["earliest_posted_date"] == "2026-01-05"
    assert chk["latest_posted_date"] == "2026-02-01"
    assert chk["latest_balance"] == "2490.00"
    assert chk["latest_balance_as_of"] == "2026-01-31"
    assert chk["mask"] == "4321"
    assert chk["visibility"] == "shared"
    assert chk["currency"] == "USD"
    emp = items["Example Empty"]
    assert emp["id"] == str(empty)
    assert emp["transaction_count"] == 0
    assert emp["latest_balance"] is None and emp["earliest_posted_date"] is None


async def test_per_person_and_household_scoping(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    me = await _person_id(owner)
    sam = (
        await owner.client.post(
            "/api/household/people", json={"display_name": "Sam Example", "filing_role": "spouse"}
        )
    ).json()
    sam_id = uuid.UUID(sam["id"])
    mine = await _account(app, hid, "Mine Private", owner_person=me, visibility="private")
    theirs = await _account(app, hid, "Theirs Private", owner_person=sam_id, visibility="private")
    summary_only = await _account(
        app, hid, "Theirs Summary", owner_person=sam_id, visibility="summary_only"
    )
    shared = await _account(app, hid, "Joint Shared")
    for acct in (mine, theirs, summary_only, shared):
        await _tx(app, hid, acct, "2026-01-05", "-1.00", f"row {acct}")

    names = {i["name"] for i in (await owner.client.get("/api/accounts")).json()["items"]}
    assert names == {"Mine Private", "Joint Shared", "Theirs Summary"}
    seen = {t["account_id"] for t in (await owner.client.get("/api/transactions")).json()["items"]}
    # Another person's private account and a summary-only account's detail are both hidden.
    assert seen == {str(mine), str(shared)}
    r = await owner.client.get("/api/transactions", params={"account_id": str(theirs)})
    assert r.json()["items"] == []
    s = await owner.client.get("/api/transactions/summary", params={"account_id": str(theirs)})
    assert s.json() == {"totals": []}

    # A second household sees nothing of this one.
    _, _, token = await seed_household(app, "Other Example Household", "Bo Other", "bo@example.com")
    async with make_client(app) as b:
        b.cookies.set("hf_session", token)
        assert (await b.get("/api/accounts")).json()["items"] == []
        assert (await b.get("/api/transactions")).json() == {"items": [], "next_cursor": None}
        assert (await b.get("/api/transactions/summary")).json() == {"totals": []}
        r = await b.get("/api/transactions", params={"account_id": str(shared)})
        assert r.json()["items"] == []


async def _seed_many(app: FastAPI, owner: Owner) -> tuple[uuid.UUID, uuid.UUID]:
    hid = uuid.UUID(owner.household["id"])
    a = await _account(app, hid, "Example Checking")
    b = await _account(app, hid, "Example Savings", currency="EUR")
    await _tx(app, hid, a, "2026-01-10", "-12.50", "Corner Coffee")
    await _tx(app, hid, a, "2026-01-10", "-30.00", "100% Fresh_Market")
    await _tx(app, hid, a, "2026-01-11", "1000.00", "Payroll Example")
    await _tx(app, hid, a, "2026-01-12", "-99.99", "Gas Co", is_pending=True)
    await _tx(app, hid, b, "2026-01-12", "20.00", "Interest", is_transfer=True)
    return a, b


async def test_pagination_is_keyset_and_stable(app: FastAPI, owner: Owner) -> None:
    await _seed_many(app, owner)
    full = (await owner.client.get("/api/transactions")).json()
    assert full["next_cursor"] is None
    assert [i["posted_date"] for i in full["items"]] == sorted(
        (i["posted_date"] for i in full["items"]), reverse=True
    )
    got: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await owner.client.get("/api/transactions", params=params)).json()
        got += [i["id"] for i in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages == 3
    assert got == [i["id"] for i in full["items"]]
    assert len(set(got)) == 5


async def test_limits_and_bad_input(owner: Owner) -> None:
    c = owner.client
    assert (await c.get("/api/transactions", params={"limit": 201})).status_code == 422
    assert (await c.get("/api/transactions", params={"limit": 0})).status_code == 422
    assert (await c.get("/api/transactions", params={"cursor": "not-a-cursor"})).status_code == 422
    assert (await c.get("/api/transactions", params={"min_amount": "1e3"})).status_code == 422
    assert (await c.get("/api/transactions", params={"min_amount": "abc"})).status_code == 422
    assert (await c.get("/api/transactions", params={"direction": "sideways"})).status_code == 422
    r = await c.get(
        "/api/transactions", params={"date_from": "2026-02-01", "date_to": "2026-01-01"}
    )
    assert r.status_code == 422
    r = await c.get("/api/transactions", params={"min_amount": "5", "max_amount": "1"})
    assert r.status_code == 422


async def test_filters(app: FastAPI, owner: Owner) -> None:
    a, b = await _seed_many(app, owner)
    c = owner.client

    async def descs(**params: Any) -> list[str]:
        r = await c.get("/api/transactions", params=params)
        assert r.status_code == 200, r.text
        return [i["description"] for i in r.json()["items"]]

    assert set(await descs(account_id=str(b))) == {"Interest"}
    both = await c.get("/api/transactions", params=[("account_id", str(a)), ("account_id", str(b))])
    assert len(both.json()["items"]) == 5
    assert set(await descs(date_from="2026-01-11", date_to="2026-01-11")) == {"Payroll Example"}
    assert set(await descs(q="coffee")) == {"Corner Coffee"}
    # Wildcards are literal: '%' and '_' match themselves only.
    assert await descs(q="%") == ["100% Fresh_Market"]
    assert await descs(q="h_M") == ["100% Fresh_Market"]
    assert await descs(q="x_") == []
    assert await descs(q="'; DROP TABLE transaction; --") == []
    assert set(await descs(direction="inflow")) == {"Payroll Example", "Interest"}
    assert set(await descs(direction="outflow")) == {"Corner Coffee", "100% Fresh_Market", "Gas Co"}
    assert set(await descs(min_amount="-30.00", max_amount="-12.50")) == {
        "Corner Coffee",
        "100% Fresh_Market",
    }
    assert set(await descs(exclude_transfers="true")) == {
        "Corner Coffee",
        "100% Fresh_Market",
        "Payroll Example",
        "Gas Co",
    }
    rows = (await c.get("/api/transactions", params={"q": "Gas"})).json()["items"]
    assert rows[0]["is_pending"] is True
    assert rows[0]["amount"] == "-99.99"
    assert rows[0]["currency"] == "USD"
    assert rows[0]["import_reconcile_status"] is None and rows[0]["needs_review"] is False


async def test_summary_groups_by_currency(app: FastAPI, owner: Owner) -> None:
    await _seed_many(app, owner)
    r = await owner.client.get("/api/transactions/summary")
    assert r.status_code == 200, r.text
    totals = {t["currency"]: t for t in r.json()["totals"]}
    assert totals["USD"] == {
        "currency": "USD",
        "inflow": "1000.00",
        "outflow": "-142.49",
        "net": "857.51",
        "count": 4,
    }
    assert totals["EUR"]["inflow"] == "20.00"
    assert totals["EUR"]["outflow"] == "0.00"
    r = await owner.client.get("/api/transactions/summary", params={"direction": "outflow"})
    assert [t["currency"] for t in r.json()["totals"]] == ["USD"]
    assert r.json()["totals"][0]["inflow"] == "0.00"


async def test_import_review_status_and_superseded_rows_hidden(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    a = await _account(app, hid, "Example Card")
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        doc = Document(
            household_id=hid,
            kind="statement",
            filename="example.pdf",
            sha256="0" * 64,
            storage_path="statements/example.pdf",
            mime="application/pdf",
            size_bytes=1,
        )
        s.add(doc)
        await s.flush()
        run = ImportRun(
            household_id=hid, document_id=doc.id, account_id=a, status="running", started_at=WHEN
        )
        s.add(run)
        await s.flush()
        run_id = run.id
        for n, amt in enumerate(("-7.00", "-8.00")):
            s.add(
                Transaction(
                    household_id=hid,
                    account_id=a,
                    posted_date=date(2026, 1, 3 + n),
                    amount=Decimal(amt),
                    description_raw=f"Imported {n}",
                    source="import",
                    import_fingerprint=_fp(n),
                    import_run_id=run_id,
                )
            )
        await s.flush()
        run.status = "succeeded"
        run.finished_at = WHEN
        run.reconcile_status = "needs_review"
    items = (await owner.client.get("/api/transactions")).json()["items"]
    assert len(items) == 2
    assert all(i["import_reconcile_status"] == "needs_review" and i["needs_review"] for i in items)
    # Supersede one: it disappears from the list, summary and counts.
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        win = Transaction(
            household_id=hid,
            account_id=a,
            posted_date=date(2026, 1, 3),
            amount=Decimal("-7.00"),
            description_raw="Plaid copy",
            source="manual",
        )
        s.add(win)
        await s.flush()
        loser = (
            await s.execute(
                Transaction.__table__.select().where(Transaction.description_raw == "Imported 0")
            )
        ).one()
        row = await s.get(Transaction, loser.id)
        assert row is not None
        row.superseded_by_id = win.id
    descs = {
        i["description"] for i in (await owner.client.get("/api/transactions")).json()["items"]
    }
    assert descs == {"Imported 1", "Plaid copy"}
    total = (await owner.client.get("/api/transactions/summary")).json()["totals"][0]
    assert total["count"] == 2 and total["outflow"] == "-15.00"


def test_openapi_money_fields_are_strings() -> None:
    app_openapi: dict[str, Any] = openapi_document()
    schemas = app_openapi["components"]["schemas"]
    for schema, fields in {
        "TransactionOut": ["amount"],
        "AccountOut": ["latest_balance"],
        "CurrencyTotals": ["inflow", "outflow", "net"],
    }.items():
        for f in fields:
            prop = schemas[schema]["properties"][f]
            types = [prop] if "type" in prop else prop.get("anyOf", [])
            assert any(t.get("type") == "string" for t in types), (schema, f, prop)
            assert all(t.get("type") in ("string", "null") for t in types), (schema, f, prop)
    params = {p["name"]: p for p in app_openapi["paths"]["/api/transactions"]["get"]["parameters"]}
    for name in ("min_amount", "max_amount"):
        branches = params[name]["schema"].get("anyOf", [params[name]["schema"]])
        assert all(b.get("type") in ("string", "null") for b in branches), name
