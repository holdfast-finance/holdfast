"""C6a: transaction category API. Scope, can_edit, filters, superseded and audit.

All values are invented. Rows are seeded as a household-wide job, then driven through the real
app as the signed-in people, so RLS applies exactly as in production.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from app.db_context import apply_context, scoped_session, unscoped_session
from app.models import Account, AppUser, AuditLog, Category, Transaction, TransactionSplit
from app.openapi_export import openapi_document
from app.services import sessions
from tests.api.conftest import Owner, make_client


async def _account(
    app: FastAPI, hid: uuid.UUID, name: str, owner_person: uuid.UUID | None, visibility: str
) -> uuid.UUID:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        a = Account(
            household_id=hid,
            owner_person_id=owner_person,
            source="manual",
            name=name,
            type="depository",
            visibility=visibility,
            currency="USD",
        )
        s.add(a)
        await s.flush()
        return a.id


async def _tx(
    app: FastAPI,
    hid: uuid.UUID,
    account_id: uuid.UUID,
    description: str,
    amount: str = "-10.00",
    **extra: Any,
) -> uuid.UUID:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        t = Transaction(
            household_id=hid,
            account_id=account_id,
            posted_date=date(2026, 1, 5),
            amount=Decimal(amount),
            description_raw=description,
            source="manual",
            **extra,
        )
        s.add(t)
        await s.flush()
        return t.id


async def _category(app: FastAPI, hid: uuid.UUID, slug: str) -> uuid.UUID:
    async with scoped_session(app.state.session_factory, hid, None) as s:
        return (await s.execute(select(Category.id).where(Category.slug == slug))).scalar_one()


class World:
    """Two people in one household, with one account of each kind."""

    def __init__(self) -> None:
        self.hid: uuid.UUID
        self.me: uuid.UUID
        self.sam: uuid.UUID
        self.sam_client: httpx.AsyncClient
        self.acct: dict[str, uuid.UUID] = {}
        self.tx: dict[str, uuid.UUID] = {}


async def _world(app: FastAPI, owner: Owner) -> World:
    w = World()
    w.hid = uuid.UUID(owner.household["id"])
    me = (await owner.client.get("/api/auth/me")).json()
    w.me = uuid.UUID(me["user"]["person"]["id"])
    sam = (
        await owner.client.post(
            "/api/household/people", json={"display_name": "Sam Example", "filing_role": "spouse"}
        )
    ).json()
    w.sam = uuid.UUID(sam["id"])
    async with unscoped_session(app.state.session_factory) as s:
        await apply_context(s, w.hid, None)
        user = AppUser(household_id=w.hid, person_id=w.sam, email="sam@example.com", role="member")
        s.add(user)
        await s.flush()
        token = await sessions.create(s, user.id, ip=None, user_agent=None, strong=True)
    w.sam_client = make_client(app)
    w.sam_client.cookies.set("hf_session", token)
    kinds = {
        "mine_private": (w.me, "private"),
        "mine_shared": (w.me, "shared"),
        "sam_private": (w.sam, "private"),
        "sam_shared": (w.sam, "shared"),
        "sam_summary": (w.sam, "summary_only"),
        "joint": (None, "shared"),
    }
    for name, (person, vis) in kinds.items():
        w.acct[name] = await _account(app, w.hid, name, person, vis)
        w.tx[name] = await _tx(app, w.hid, w.acct[name], f"Example Store {name}")
    return w


async def test_can_edit_follows_ownership_not_visibility(app: FastAPI, owner: Owner) -> None:
    w = await _world(app, owner)
    try:
        mine = {
            t["id"]: t["can_edit"]
            for t in (await owner.client.get("/api/transactions")).json()["items"]
        }
        # I see my own two, the joint one and Sam's shared one; Sam's private and summary-only
        # rows are not in my list at all.
        assert mine == {
            str(w.tx["mine_private"]): True,
            str(w.tx["mine_shared"]): True,
            str(w.tx["joint"]): True,
            str(w.tx["sam_shared"]): False,
        }
        theirs = {
            t["id"]: t["can_edit"]
            for t in (await w.sam_client.get("/api/transactions")).json()["items"]
        }
        assert theirs == {
            str(w.tx["sam_private"]): True,
            str(w.tx["sam_shared"]): True,
            str(w.tx["sam_summary"]): True,
            str(w.tx["joint"]): True,
            str(w.tx["mine_shared"]): False,
        }
    finally:
        await w.sam_client.aclose()


async def test_other_persons_rows_are_invisible_and_not_editable(
    app: FastAPI, owner: Owner
) -> None:
    w = await _world(app, owner)
    try:
        cat = await _category(app, w.hid, "food.groceries")
        for key in ("sam_private", "sam_summary"):
            tid = w.tx[key]
            assert (await owner.client.get(f"/api/transactions/{tid}")).status_code == 404
            r = await owner.client.put(
                f"/api/transactions/{tid}/category", json={"category_id": str(cat)}
            )
            assert r.status_code == 404, key
            assert (
                await owner.client.post(f"/api/transactions/{tid}/category/release")
            ).status_code == 404
        # Visible (shared) but another person's account: read yes, write 404, nothing changed.
        tid = w.tx["sam_shared"]
        got = await owner.client.get(f"/api/transactions/{tid}")
        assert got.status_code == 200 and got.json()["can_edit"] is False
        r = await owner.client.put(
            f"/api/transactions/{tid}/category", json={"category_id": str(cat)}
        )
        assert r.status_code == 404
        assert (
            await owner.client.post(f"/api/transactions/{tid}/category/release")
        ).status_code == 404
        async with scoped_session(app.state.session_factory, w.hid, None, household_wide=True) as s:
            for key in ("sam_private", "sam_summary", "sam_shared"):
                row = await s.get(Transaction, w.tx[key])
                assert row is not None
                assert row.category_id is None and row.category_source is None
    finally:
        await w.sam_client.aclose()


async def test_set_clear_release_and_audit(app: FastAPI, owner: Owner) -> None:
    w = await _world(app, owner)
    try:
        cat = await _category(app, w.hid, "food.groceries")
        tid = w.tx["mine_private"]
        r = await owner.client.put(
            f"/api/transactions/{tid}/category", json={"category_id": str(cat)}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["category_id"] == str(cat) and body["category_source"] == "user"
        assert body["amount"] == "-10.00" and body["can_edit"] is True
        assert body["merchant_key"] is not None
        assert body["has_splits"] is False

        r = await owner.client.post(f"/api/transactions/{tid}/category/release")
        assert r.status_code == 200
        assert r.json()["category_source"] is None and r.json()["category_id"] == str(cat)

        r = await owner.client.put(f"/api/transactions/{tid}/category", json={"category_id": None})
        assert r.status_code == 200
        assert r.json()["category_id"] is None and r.json()["category_source"] == "user"

        r = await owner.client.put(
            f"/api/transactions/{tid}/category", json={"category_id": str(uuid.uuid4())}
        )
        assert r.status_code == 404
        r = await owner.client.put(f"/api/transactions/{tid}/category", json={})
        assert r.status_code == 422

        async with scoped_session(app.state.session_factory, w.hid, None, household_wide=True) as s:
            actions = list(
                (await s.execute(select(AuditLog.action).where(AuditLog.entity_id == str(tid))))
                .scalars()
                .all()
            )
        assert actions.count("ledger.category_set") == 2
        assert actions.count("ledger.category_released") == 1
    finally:
        await w.sam_client.aclose()


async def test_superseded_is_409_on_write_404_on_read(app: FastAPI, owner: Owner) -> None:
    w = await _world(app, owner)
    try:
        cat = await _category(app, w.hid, "food.groceries")
        acct = w.acct["mine_private"]
        winner = await _tx(app, w.hid, acct, "Plaid copy")
        loser = await _tx(app, w.hid, acct, "Import copy")
        async with scoped_session(app.state.session_factory, w.hid, None, household_wide=True) as s:
            row = await s.get(Transaction, loser)
            assert row is not None
            row.superseded_by_id = winner
        r = await owner.client.put(
            f"/api/transactions/{loser}/category", json={"category_id": str(cat)}
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
        assert (
            await owner.client.post(f"/api/transactions/{loser}/category/release")
        ).status_code == 409
        assert (await owner.client.get(f"/api/transactions/{loser}")).status_code == 404
    finally:
        await w.sam_client.aclose()


async def test_filters_and_has_splits(app: FastAPI, owner: Owner) -> None:
    w = await _world(app, owner)
    try:
        groceries = await _category(app, w.hid, "food.groceries")
        coffee = await _category(app, w.hid, "food.coffee")
        acct = w.acct["mine_private"]
        a = await _tx(app, w.hid, acct, "Filter A", category_id=groceries, category_source="rule")
        b = await _tx(app, w.hid, acct, "Filter B", category_id=coffee, category_source="user")
        c = await _tx(app, w.hid, acct, "Filter C", is_transfer=True)
        d = await _tx(app, w.hid, acct, "Filter D", amount="-20.00")
        async with scoped_session(app.state.session_factory, w.hid, None, household_wide=True) as s:
            s.add_all(
                [
                    TransactionSplit(
                        household_id=w.hid,
                        transaction_id=d,
                        amount=Decimal("-12.00"),
                        category_id=groceries,
                    ),
                    TransactionSplit(
                        household_id=w.hid,
                        transaction_id=d,
                        amount=Decimal("-8.00"),
                        category_id=coffee,
                    ),
                ]
            )

        async def ids(**params: Any) -> set[str]:
            params["account_id"] = str(acct)
            r = await owner.client.get("/api/transactions", params=params)
            assert r.status_code == 200, r.text
            return {t["id"] for t in r.json()["items"]}

        assert await ids(category_id=[str(groceries)]) == {str(a)}
        assert await ids(category_id=[str(groceries), str(coffee)]) == {str(a), str(b)}
        assert await ids(category_source="rule") == {str(a)}
        assert await ids(uncategorized="true") == {str(w.tx["mine_private"]), str(c), str(d)}
        assert str(c) in await ids(transfer="only") and str(a) not in await ids(transfer="only")
        assert str(c) not in await ids(transfer="exclude")
        flags = {
            t["id"]: t["has_splits"]
            for t in (
                await owner.client.get("/api/transactions", params={"account_id": str(acct)})
            ).json()["items"]
        }
        assert flags[str(d)] is True and flags[str(a)] is False

        for bad in (
            {"uncategorized": "true", "category_id": str(groceries)},
            {"uncategorized": "true", "category_source": "user"},
            {"transfer": "only", "exclude_transfers": "true"},
            {"transfer": "sideways"},
            {"category_source": "bogus"},
        ):
            r = await owner.client.get("/api/transactions", params=bad)
            assert r.status_code == 422, bad

        s_all = await owner.client.get(
            "/api/transactions/summary", params={"uncategorized": "true"}
        )
        assert s_all.status_code == 200
    finally:
        await w.sam_client.aclose()


async def test_no_person_cannot_edit(app: FastAPI, owner: Owner) -> None:
    from app.services.ledger import LedgerService
    from app.services.sessions import Principal

    w = await _world(app, owner)
    try:
        p = Principal(
            session_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            household_id=w.hid,
            person_id=None,
            role="member",
            email="x@example.com",
            reauth_at=None,
        )
        svc = LedgerService(None, p)  # type: ignore[arg-type]
        assert svc._can_edit(None) is False
        assert svc._can_edit(w.me) is False
    finally:
        await w.sam_client.aclose()


def test_openapi_has_category_fields() -> None:
    schemas = openapi_document()["components"]["schemas"]
    props = schemas["TransactionOut"]["properties"]
    for f in ("merchant_key", "category_id", "category_source", "has_splits", "can_edit"):
        assert f in props, f
    assert "CategoryChoiceIn" in schemas
