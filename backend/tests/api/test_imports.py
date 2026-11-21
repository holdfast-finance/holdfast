"""Statement import HTTP API (I5b): upload, dedupe, size caps, runs, accept, tenant isolation.

PDFs are synthetic: the statement texts from the I1 profile tests, drawn with reportlab. Every
name, number and amount is invented.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

from app.db_context import scoped_session
from app.importers.pdf_text import MAX_PDF_BYTES
from app.models import AuditLog, Document, ImportRun, Transaction
from app.security.body_limit import MAX_BODY_BYTES, UPLOAD_MAX_BODY_BYTES
from app.services.imports import sanitise_filename
from tests.api.conftest import Owner, age_sessions, make_client, seed_household
from tests.importers.test_statement_profiles import DEBIT, NFCU_CARD

MIB = 1024 * 1024
BAD_CARD = NFCU_CARD.replace("New Balance   $245.00", "New Balance   $300.00")
OTHER_CARD = NFCU_CARD.replace("$100.00", "$90.00").replace("$245.00", "$235.00")


def pdf_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, invariant=1)  # byte-identical output, so SHA-256 repeats
    c.setFont("Courier", 8)
    for i, line in enumerate(text.splitlines()):
        c.drawString(20, 800 - 11 * i, line)
    c.save()
    return buf.getvalue()


@pytest.fixture(autouse=True)
def store(app: FastAPI, tmp_path: Path) -> Path:
    app.state.settings.document_store = tmp_path / "docs"
    return tmp_path / "docs"


async def upload(
    client: httpx.AsyncClient,
    data: bytes,
    name: str = "statement.pdf",
    content_type: str = "application/pdf",
) -> httpx.Response:
    return await client.post("/api/imports/statements", files={"file": (name, data, content_type)})


def stored_files(store: Path) -> list[Path]:
    root = store / "statements"
    return sorted(root.iterdir()) if root.exists() else []


async def count(app: FastAPI, hid: uuid.UUID, model: type) -> int:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


async def other_household(app: FastAPI) -> httpx.AsyncClient:
    _, _, token = await seed_household(app, "Other Example Household", "Bo Other", "bo@example.com")
    client = make_client(app)
    client.cookies.set("hf_session", token)
    return client


# --------------------------------------------------------------------------- upload


async def test_upload_returns_blocks_with_string_money(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    r = await upload(owner.client, pdf_bytes(NFCU_CARD), "Example Card.pdf")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["created"], body["balances_only"], body["filename"]) == (
        True,
        False,
        "Example Card.pdf",
    )
    (block,) = body["blocks"]
    assert block["reconcile_status"] == "reconciled"
    assert (block["rows_parsed"], block["rows_inserted"], block["rows_duplicate"]) == (3, 3, 0)
    assert block["created"] is True
    assert block["opening_balance"] == "-200.00"
    assert block["closing_balance"] == "-245.00"
    assert block["txn_sum"] == "-45.00"
    assert block["balance_diff"] == "0.00"
    # Stored by hash under the document store, not by any client-supplied name.
    (path,) = stored_files(store)
    assert path.name.endswith(".pdf") and len(path.stem) == 64
    hid = uuid.UUID(owner.household["id"])
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        doc = (await s.execute(select(Document))).scalar_one()
        assert doc.storage_path == f"statements/{path.name}"
        audit = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "import.upload")))
            .scalars()
            .all()
        )
    assert len(audit) == 1
    assert "Example Card" not in str(audit[0].detail)
    # The rows are in the ledger.
    tx = await owner.client.get("/api/transactions")
    assert len(tx.json()["items"]) == 3


async def test_reupload_is_a_noop_that_returns_the_same_document(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    hid = uuid.UUID(owner.household["id"])
    data = pdf_bytes(NFCU_CARD)
    first = (await upload(owner.client, data)).json()
    again = await upload(owner.client, data, "renamed copy.pdf")
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["created"] is False
    assert body["document_id"] == first["document_id"]
    assert [b["created"] for b in body["blocks"]] == [False]
    assert body["blocks"][0]["run_id"] == first["blocks"][0]["run_id"]
    assert body["blocks"][0]["rows_inserted"] == 0
    assert await count(app, hid, Document) == 1
    assert await count(app, hid, ImportRun) == 1
    assert await count(app, hid, Transaction) == 3
    assert len(stored_files(store)) == 1


async def test_non_pdf_with_a_pdf_name_is_refused_and_stores_nothing(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    r = await upload(owner.client, b"MZ\x90\x00 not a pdf at all", "statement.pdf")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    wrong_type = await upload(owner.client, pdf_bytes(NFCU_CARD), "s.pdf", "text/plain")
    assert wrong_type.status_code == 422
    assert (await upload(owner.client, b"", "empty.pdf")).status_code == 422
    # Looks like a PDF but isn't readable.
    broken = await upload(owner.client, b"%PDF-1.4\n" + b"garbage" * 50)
    assert broken.status_code == 422
    # A readable PDF no profile matches.
    unknown = await upload(owner.client, pdf_bytes("Hello example\nNothing to see"))
    assert unknown.status_code == 422
    assert stored_files(store) == []
    hid = uuid.UUID(owner.household["id"])
    assert await count(app, hid, Document) == 0


async def test_not_multipart_and_missing_file_are_validation_errors(owner: Owner) -> None:
    r = await owner.client.post("/api/imports/statements", json={"file": "x"})
    assert r.status_code == 422
    r = await owner.client.post(
        "/api/imports/statements", data={"other": "x"}, files={"x": ("a", b"b")}
    )
    assert r.status_code == 422


async def test_oversize_by_real_bytes_with_a_lying_content_length(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    boundary = "xBOUNDARYx"

    async def body() -> AsyncIterator[bytes]:
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.pdf"\r\n'
            "Content-Type: application/pdf\r\n\r\n%PDF-1.4\n"
        ).encode()
        for _ in range(UPLOAD_MAX_BODY_BYTES // MIB + 2):
            yield b"x" * MIB
        yield f"\r\n--{boundary}--\r\n".encode()

    r = await owner.client.post(
        "/api/imports/statements",
        content=body(),
        headers={
            "content-type": f"multipart/form-data; boundary={boundary}",
            "content-length": "1000",  # a lie: the real body is over 21 MiB
        },
    )
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    assert stored_files(store) == []


async def test_file_over_20_mib_but_under_the_body_cap_is_refused(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    r = await upload(owner.client, b"%PDF-1.4\n" + b"x" * (MAX_PDF_BYTES + 10))
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"
    assert stored_files(store) == []


async def test_larger_body_is_allowed_only_on_the_upload_route(owner: Owner) -> None:
    assert UPLOAD_MAX_BODY_BYTES == MAX_PDF_BYTES + MIB
    big = b'{"x": "' + b"a" * (2 * MIB) + b'"}'
    r = await owner.client.post(
        "/api/household/people", content=big, headers={"content-type": "application/json"}
    )
    assert r.status_code == 413
    assert MAX_BODY_BYTES == MIB
    # A 2 MiB PDF body passes the body cap on the upload route (then fails as unreadable).
    r = await upload(owner.client, b"%PDF-1.4\n" + b"x" * (2 * MIB))
    assert r.status_code == 422


async def test_upload_needs_a_session_and_is_rate_limited(
    client: httpx.AsyncClient, owner: Owner
) -> None:
    anon = await upload(
        client.__class__(
            transport=client._transport, base_url=str(client.base_url), headers=dict(client.headers)
        ),
        b"%PDF-",
    )
    assert anon.status_code == 401
    codes = [(await upload(owner.client, b"nope")).status_code for _ in range(11)]
    assert codes[:10] == [422] * 10
    assert codes[10] == 429


async def test_filename_is_sanitised_and_never_picks_the_path(owner: Owner, store: Path) -> None:
    assert sanitise_filename("../../etc/passwd") == "passwd.pdf"
    assert sanitise_filename("C:\\Users\\x\\a b<script>.PDF") == "a b_script.pdf"
    assert sanitise_filename("\x00\n..") == "statement.pdf"
    assert len(sanitise_filename("a" * 500 + ".pdf")) <= 100
    r = await upload(owner.client, pdf_bytes(NFCU_CARD), "../../../etc/cron.d/evil.pdf")
    assert r.status_code == 200
    assert r.json()["filename"] == "evil.pdf"
    assert all(p.parent == store / "statements" for p in stored_files(store))


async def test_names_stay_out_of_logs(
    owner: Owner, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level("DEBUG")
    await upload(owner.client, pdf_bytes(NFCU_CARD), "SECRETNAME-1234.pdf")
    await upload(owner.client, b"not a pdf", "SECRETNAME-5678.pdf")
    out = capfd.readouterr()
    assert "SECRETNAME" not in caplog.text + out.out + out.err


# --------------------------------------------------------------------------- runs and accept


async def test_runs_are_newest_first_and_keyset_paged(owner: Owner) -> None:
    for text in (NFCU_CARD, OTHER_CARD, DEBIT):  # DEBIT has two blocks: four runs in all
        assert (await upload(owner.client, pdf_bytes(text))).status_code == 200
    full = (await owner.client.get("/api/imports/runs")).json()
    assert full["next_cursor"] is None
    times = [(i["created_at"], i["id"]) for i in full["items"]]
    assert len(times) == 4 and times == sorted(times, reverse=True)
    first = full["items"][0]
    assert first["filename"] == "statement.pdf" and first["account_name"]
    assert first["balance_diff"] == "0.00"
    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = (await owner.client.get("/api/imports/runs", params=params)).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [i["id"] for i in full["items"]]
    bad = await owner.client.get("/api/imports/runs", params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422


async def test_accept_needs_review_then_again_is_idempotent(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    up = (await upload(owner.client, pdf_bytes(BAD_CARD))).json()
    (block,) = up["blocks"]
    assert block["reconcile_status"] == "needs_review"
    assert block["balance_diff"] != "0.00"
    run_id = block["run_id"]

    first = await owner.client.post(f"/api/imports/runs/{run_id}/accept")
    assert first.status_code == 200, first.text
    run = first.json()
    assert run["reconcile_status"] == "accepted" and run["reviewed_at"]
    # Accepting is not reconciling: the numbers still show the gap.
    assert run["balance_diff"] == block["balance_diff"] != "0.00"
    again = await owner.client.post(f"/api/imports/runs/{run_id}/accept")
    assert again.status_code == 200
    assert again.json() == run
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "import.accept")))
            .scalars()
            .all()
        )
        stored = (await s.execute(select(ImportRun))).scalar_one()
    assert len(rows) == 1  # the repeat wrote no second audit row
    assert str(rows[0].entity_id) == run_id and rows[0].user_id is not None
    assert stored.reviewed_by_user_id == rows[0].user_id
    listed = (await owner.client.get("/api/imports/runs")).json()["items"][0]
    assert listed["reconcile_status"] == "accepted"
    # A re-upload still reports the accepted state, with nothing written.
    re = (await upload(owner.client, pdf_bytes(BAD_CARD))).json()
    assert re["created"] is False and re["blocks"][0]["reconcile_status"] == "accepted"


async def test_accept_needs_fresh_step_up_and_a_reviewable_run(app: FastAPI, owner: Owner) -> None:
    ok = (await upload(owner.client, pdf_bytes(NFCU_CARD))).json()["blocks"][0]["run_id"]
    review = (await upload(owner.client, pdf_bytes(BAD_CARD))).json()["blocks"][0]["run_id"]
    # A reconciled run has nothing to accept.
    r = await owner.client.post(f"/api/imports/runs/{ok}/accept")
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    await age_sessions(app, reauth_at=datetime.now(UTC) - timedelta(minutes=30))
    stale = await owner.client.post(f"/api/imports/runs/{review}/accept")
    assert stale.status_code == 403 and stale.json()["error"]["code"] == "reauth_required"
    # Upload itself needs no step-up.
    assert (await upload(owner.client, pdf_bytes(OTHER_CARD))).status_code == 200
    missing = await owner.client.post(f"/api/imports/runs/{uuid.uuid4()}/accept")
    assert missing.status_code in (403, 404)


async def test_accept_unknown_run_is_404(owner: Owner) -> None:
    r = await owner.client.post(f"/api/imports/runs/{uuid.uuid4()}/accept")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_routes_require_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/imports/runs")).status_code == 401
    assert (await client.post(f"/api/imports/runs/{uuid.uuid4()}/accept")).status_code == 401


# --------------------------------------------------------------------------- isolation


async def test_another_household_cannot_list_accept_or_learn_of_runs(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    data = pdf_bytes(BAD_CARD)
    run_id = (await upload(owner.client, data)).json()["blocks"][0]["run_id"]
    async with await other_household(app) as b:
        assert (await b.get("/api/imports/runs")).json() == {"items": [], "next_cursor": None}
        r = await b.post(f"/api/imports/runs/{run_id}/accept")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
        # Their own upload of the same bytes is a fresh import: no hint that A has it.
        mine = await upload(b, data)
        assert mine.status_code == 200 and mine.json()["created"] is True
        assert mine.json()["blocks"][0]["run_id"] != run_id
        assert len((await b.get("/api/imports/runs")).json()["items"]) == 1
    # A's run is untouched.
    a = (await owner.client.get("/api/imports/runs")).json()["items"]
    assert [i["reconcile_status"] for i in a] == ["needs_review"]
