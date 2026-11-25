"""A1: PATCH /accounts/{id} owner and visibility.

Synthetic people and amounts only. The service never widens the session, and the
joint-claim role check lives here rather than in the database (ADR 0008).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from app.db_context import apply_context, scoped_session, unscoped_session
from app.models import Account, AppUser, AuditLog, Document, ImportRun, Person, Transaction
from app.services import sessions
from app.services.accounts import ACCOUNT_LOCK
from tests.api.conftest import Owner, make_client
from tests.api.test_imports import BAD_CARD, pdf_bytes, upload

WHEN = datetime(2026, 4, 2, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "app" / "services" / "accounts.py"


@pytest.fixture(autouse=True)
def store(app: FastAPI, tmp_path: Path) -> Path:
    """The upload test writes synthetic PDFs. Nothing here is a real statement."""
    app.state.settings.document_store = tmp_path / "docs"
    return tmp_path / "docs"


def test_service_does_not_mention_household_wide_and_locks_without_key_update() -> None:
    text = SERVICE.read_text()
    assert "household_wide" not in text
    assert ACCOUNT_LOCK == "FOR NO KEY UPDATE"
    assert "with_for_update" not in text


async def _person_id(client: httpx.AsyncClient) -> uuid.UUID:
    me = (await client.get("/api/auth/me")).json()
    return uuid.UUID(me["user"]["person"]["id"])


async def _member(app: FastAPI, hid: uuid.UUID) -> tuple[uuid.UUID, httpx.AsyncClient]:
    async with unscoped_session(app.state.session_factory) as s:
        await apply_context(s, hid, None)
        person = Person(household_id=hid, display_name="Sam Example", filing_role="spouse")
        s.add(person)
        await s.flush()
        user = AppUser(
            household_id=hid, person_id=person.id, email="sam@example.com", role="member"
        )
        s.add(user)
        await s.flush()
        token = await sessions.create(s, user.id, ip=None, user_agent=None, strong=True)
        person_id = person.id
    client = make_client(app)
    client.cookies.set("hf_session", token)
    return person_id, client


async def _account(
    app: FastAPI,
    hid: uuid.UUID,
    name: str,
    *,
    owner_person: uuid.UUID | None,
    visibility: str = "private",
) -> uuid.UUID:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        row = Account(
            household_id=hid,
            owner_person_id=owner_person,
            source="manual",
            name=name,
            type="depository",
            visibility=visibility,
            currency="USD",
            mask="0000",
        )
        s.add(row)
        await s.flush()
        return row.id


async def _patch(
    client: httpx.AsyncClient, account_id: uuid.UUID, body: dict[str, str]
) -> httpx.Response:
    return await client.patch(f"/api/accounts/{account_id}", json=body)


async def _audit_count(app: FastAPI, hid: uuid.UUID) -> int:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "account.ownership_changed")
                )
            ).scalar_one()
        )


async def test_owner_makes_account_joint_and_repeat_is_a_noop(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    me = await _person_id(owner.client)
    account_id = await _account(app, hid, "Example Checking", owner_person=me, visibility="private")
    listed = {i["name"]: i for i in (await owner.client.get("/api/accounts")).json()["items"]}
    before = listed["Example Checking"]
    assert before["owner"] == "me" and before["owner_person_id"] == str(me)
    assert before["can_edit"] is True and before["can_change_owner"] is True

    first = await _patch(owner.client, account_id, {"owner": "joint"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["owner"] == "joint" and body["owner_person_id"] is None
    assert body["visibility"] == "private"
    assert body["can_edit"] is True and body["can_change_owner"] is True
    again = await _patch(owner.client, account_id, {"owner": "joint"})
    assert again.status_code == 200
    assert await _audit_count(app, hid) == 1
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        row = (
            await s.execute(select(AuditLog).where(AuditLog.action == "account.ownership_changed"))
        ).scalar_one()
    assert set(row.detail) == {
        "outcome",
        "account_id",
        "owner_kind_before",
        "owner_kind_after",
        "visibility_before",
        "visibility_after",
    }
    assert row.detail["account_id"] == str(account_id)
    assert row.detail["owner_kind_before"] == "me" and row.detail["owner_kind_after"] == "joint"
    assert "0000" not in str(row.detail)


async def test_claim_visibility_and_unknown_owner_shape(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    me = await _person_id(owner.client)
    sam_id, sam = await _member(app, hid)
    try:
        joint = await _account(app, hid, "Example Joint", owner_person=None, visibility="private")
        mine = await _account(app, hid, "Example Mine", owner_person=me)
        theirs = await _account(
            app, hid, "Example Theirs", owner_person=sam_id, visibility="private"
        )
        summary = await _account(
            app, hid, "Example Summary", owner_person=sam_id, visibility="summary_only"
        )

        refused = await _patch(sam, joint, {"owner": "me"})
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "forbidden"

        vis = await _patch(owner.client, joint, {"visibility": "shared"})
        assert vis.status_code == 422
        assert vis.json()["error"]["code"] == "validation_error"

        both = await _patch(owner.client, joint, {"owner": "joint", "visibility": "shared"})
        assert both.status_code == 422

        claim = await _patch(owner.client, joint, {"owner": "me"})
        assert claim.status_code == 200, claim.text
        assert claim.json()["owner"] == "me"
        assert claim.json()["owner_person_id"] == str(me)
        assert claim.json()["visibility"] == "shared"

        bad = await _patch(owner.client, mine, {"owner": str(sam_id)})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
        assert str(sam_id) not in bad.text

        empty = await _patch(owner.client, mine, {})
        assert empty.status_code == 422

        missing = await _patch(owner.client, uuid.uuid4(), {"visibility": "private"})
        assert missing.status_code == 404
        hidden = await _patch(owner.client, theirs, {"visibility": "shared"})
        assert hidden.status_code == 404
        summed = await _patch(owner.client, summary, {"visibility": "shared"})
        assert summed.status_code == 404

        seen = {i["id"]: i for i in (await owner.client.get("/api/accounts")).json()["items"]}
        assert seen[str(summary)]["owner"] == "other"
        assert seen[str(summary)]["can_edit"] is False
        assert seen[str(summary)]["can_change_owner"] is False
        assert str(theirs) not in seen
    finally:
        await sam.aclose()


async def test_pairs_block_an_owner_change(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    me = await _person_id(owner.client)
    account_id = await _account(app, hid, "Example Paired", owner_person=me)
    other = await _account(app, hid, "Example Other", owner_person=me)
    group = uuid.uuid4()
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        for acct, amount in ((account_id, Decimal("-5.00")), (other, Decimal("5.00"))):
            s.add(
                Transaction(
                    household_id=hid,
                    account_id=acct,
                    posted_date=WHEN.date(),
                    amount=amount,
                    description_raw="Example transfer",
                    source="manual",
                    is_transfer=True,
                    transfer_group_id=group,
                )
            )
    blocked = await _patch(owner.client, account_id, {"owner": "joint"})
    assert blocked.status_code == 409
    assert blocked.json()["error"] == {
        "code": "conflict",
        "message": "This account has paired transfers.",
    }
    # Visibility alone does not touch pairs.
    ok = await _patch(owner.client, account_id, {"visibility": "shared"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["owner"] == "me" and ok.json()["visibility"] == "shared"


async def test_claim_waits_for_the_other_persons_open_import(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    sam_id, sam = await _member(app, hid)
    try:
        joint = await _account(app, hid, "Example Shared", owner_person=None, visibility="shared")
        async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
            doc = Document(
                household_id=hid,
                person_id=sam_id,
                kind="statement",
                filename="example.pdf",
                sha256="ab" * 32,
                storage_path="statements/example-sam.pdf",
                mime="application/pdf",
                size_bytes=1,
            )
            s.add(doc)
            await s.flush()
            run = ImportRun(
                household_id=hid,
                document_id=doc.id,
                account_id=joint,
                status="succeeded",
                reconcile_status="needs_review",
                started_at=WHEN,
                finished_at=WHEN,
            )
            s.add(run)
            await s.flush()
            run_id = run.id
        blocked = await _patch(owner.client, joint, {"owner": "me"})
        assert blocked.status_code == 409
        err = blocked.json()["error"]
        assert err == {
            "code": "conflict",
            "message": "An import on this account isn't finished yet.",
        }
        assert str(run_id) not in blocked.text and str(sam_id) not in blocked.text
        async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
            stored = await s.get(ImportRun, run_id)
            assert stored is not None
            stored.reconcile_status = "accepted"
            stored.reviewed_at = WHEN
            stored.reviewed_by_user_id = uuid.UUID(owner.user["id"])
        claimed = await _patch(owner.client, joint, {"owner": "me"})
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["owner"] == "me"
    finally:
        await sam.aclose()


async def test_joint_account_accepts_both_peoples_statements(app: FastAPI, owner: Owner) -> None:
    up = await upload(owner.client, pdf_bytes(BAD_CARD))
    assert up.status_code == 200, up.text
    block = up.json()["blocks"][0]
    assert block["account_owner"] == "me" and block["account_visibility"] == "private"
    account_id = uuid.UUID(block["account_id"])
    made = await _patch(owner.client, account_id, {"owner": "joint"})
    assert made.status_code == 200, made.text

    hid = uuid.UUID(owner.household["id"])
    _sam_id, sam = await _member(app, hid)
    try:
        second = await upload(sam, pdf_bytes(BAD_CARD.replace("$80.00", "$70.00")))
        assert second.status_code == 200, second.text
        other = second.json()["blocks"][0]
        assert other["account_id"] == str(account_id)
        assert other["account_owner"] == "joint"
        assert other["reconcile_status"] == "needs_review"
        mine = await owner.client.post(f"/api/imports/runs/{block['run_id']}/accept")
        theirs = await sam.post(f"/api/imports/runs/{other['run_id']}/accept")
        assert mine.status_code == 200, mine.text
        assert theirs.status_code == 200, theirs.text
        assert mine.json()["reconcile_status"] == "accepted"
        assert theirs.json()["reconcile_status"] == "accepted"
    finally:
        await sam.aclose()
