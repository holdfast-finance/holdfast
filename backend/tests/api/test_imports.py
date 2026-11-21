"""Statement import HTTP API (I5b): upload, dedupe, size caps, runs, accept, tenant isolation.

PDFs are synthetic: the statement texts from the I1 profile tests, drawn with reportlab. Every
name, number and amount is invented.
"""

from __future__ import annotations

import asyncio
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
from app.services.errors import ApiError
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
    """Every file under statements/, temp files included."""
    root = store / "statements"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


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
        assert path.parent.name == str(hid)
        assert doc.storage_path == f"statements/{hid}/{path.name}"
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
    assert all(p.parent.parent == store / "statements" for p in stored_files(store))


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


# --------------------------------------------------------------------------- review follow-ups

SUMMARY = (
    Path(__file__).resolve().parents[1] / "fixtures" / "statements" / "current"
    / "account_summary.sanitized.txt"
)  # fmt: skip


async def audit_rows(app: FastAPI, hid: uuid.UUID, action: str) -> list[AuditLog]:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        return list((await s.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


async def test_eleventh_request_is_429_and_its_body_is_never_read(owner: Owner) -> None:
    for _ in range(10):
        assert (await upload(owner.client, b"nope")).status_code == 422
    read = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal read
        read = True
        yield b"--b\r\n"

    r = await owner.client.post(
        "/api/imports/statements",
        content=body(),
        headers={"content-type": "multipart/form-data; boundary=b"},
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in r.headers
    assert read is False


async def test_parse_slots_are_bounded_and_a_busy_server_says_so(
    owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import imports

    monkeypatch.setattr(imports, "SLOT_WAIT_SECONDS", 0.05)
    async with imports.parse_slot(), imports.parse_slot():
        r = await upload(owner.client, pdf_bytes(NFCU_CARD))
        assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert (await upload(owner.client, pdf_bytes(NFCU_CARD))).status_code == 200


async def test_parse_timeout_answers_now_but_holds_the_slot_until_the_thread_ends(
    owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from app.services import imports

    monkeypatch.setattr(imports, "PARSE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(imports, "SLOT_WAIT_SECONDS", 0.05)
    real = imports._parse_pdf

    def slow(path: Path) -> object:
        time.sleep(0.6)
        return real(path)

    monkeypatch.setattr(imports, "_parse_pdf", slow)
    r = await upload(owner.client, pdf_bytes(NFCU_CARD))
    assert r.status_code == 422 and "too long" in r.json()["error"]["message"]
    # One of the two slots is still held by the running thread, so a second holder times out.
    async with imports.parse_slot():
        with pytest.raises(ApiError):
            async with imports.parse_slot():
                pass
    await asyncio.sleep(0.9)  # the thread ends and returns its slot
    async with imports.parse_slot(), imports.parse_slot():
        pass


async def test_concurrent_identical_uploads_one_wins_one_conflicts_file_stays(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    hid = uuid.UUID(owner.household["id"])
    data = pdf_bytes(NFCU_CARD)
    a, b = await asyncio.gather(upload(owner.client, data), upload(owner.client, data))
    assert sorted([a.status_code, b.status_code]) == [200, 409], (a.text, b.text)
    assert await count(app, hid, Document) == 1
    (path,) = stored_files(store)
    assert path.read_bytes() == data
    # A later, sequential re-upload is the ordinary idempotent 200.
    again = await upload(owner.client, data)
    assert again.status_code == 200 and again.json()["created"] is False


async def test_a_failed_import_leaves_the_stored_file_and_the_sweep_removes_orphans(
    app: FastAPI, owner: Owner, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time

    from app.importers.store_sweep import sweep_store
    from app.services import imports

    hid = uuid.UUID(owner.household["id"])
    # One real document (kept) and, beside it, a failed import (orphan).
    assert (await upload(owner.client, pdf_bytes(NFCU_CARD))).status_code == 200

    async def boom(*a: object, **k: object) -> None:
        raise RuntimeError("never shown")

    real_import = imports.import_statement
    monkeypatch.setattr(imports, "import_statement", boom)
    with pytest.raises(RuntimeError):
        await upload(owner.client, pdf_bytes(OTHER_CARD))
    files = stored_files(store)
    assert len(files) == 2  # the orphan was not unlinked in the request path
    monkeypatch.setattr(imports, "import_statement", real_import)

    root = store / "statements"
    flat = root / ("a" * 64 + ".pdf")  # earlier flat layout: never touched
    flat.write_bytes(b"%PDF-")
    stale = root / ".upload-old.pdf"
    stale.write_bytes(b"x")
    fresh = root / ".upload-new.pdf"
    fresh.write_bytes(b"x")
    long_ago = time.time() - 7200
    for p in (*files, flat, stale):
        os.utime(p, (long_ago, long_ago))

    swept = await sweep_store(app.state.session_factory, store)
    assert (swept.orphans_removed, swept.temps_removed) == (1, 1)
    assert len(stored_files(store)) == 3  # kept document, flat-layout file, fresh temp
    assert flat.exists() and fresh.exists() and not stale.exists()
    assert await count(app, hid, Document) == 1
    # Too young to sweep: nothing more goes.
    assert (await sweep_store(app.state.session_factory, store)).orphans_removed == 0


async def test_overflow_amount_is_refused_and_nothing_reaches_the_logs(
    app: FastAPI,
    owner: Owner,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level("DEBUG")
    huge = NFCU_CARD.replace("$100.00", "$1,000,000,000,000.00").replace("$245.00", "$9.00")
    r = await upload(owner.client, pdf_bytes(huge), "SECRETNAME-9.pdf")
    assert r.status_code == 422, r.text
    assert r.json()["error"]["message"] == "An amount in that statement is out of range."
    out = capfd.readouterr()
    logged = caplog.text + out.out + out.err
    for needle in ("1,000,000,000,000", "1000000000000", "SECRETNAME", "Example Shop"):
        assert needle not in logged, needle
    assert await count(app, uuid.UUID(owner.household["id"]), ImportRun) == 0


async def test_only_a_duplicate_document_is_a_409_other_db_errors_are_content_free(
    owner: Owner,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from psycopg.errors import NumericValueOutOfRange
    from sqlalchemy.exc import DataError

    from app.services import imports

    async def leaky(*a: object, **k: object) -> None:
        raise DataError(
            "INSERT ... SECRETAMOUNT",
            {"amount": "SECRETAMOUNT"},
            NumericValueOutOfRange("SECRETAMOUNT"),
        )

    monkeypatch.setattr(imports, "import_statement", leaky)
    caplog.set_level("DEBUG")
    r = await upload(owner.client, pdf_bytes(NFCU_CARD), "SECRETNAME-9.pdf")
    assert r.status_code == 500
    assert r.json() == {
        "error": {"code": "server_error", "message": "The statement couldn't be saved."}
    }
    out = capfd.readouterr()
    logged = caplog.text + out.out + out.err
    assert "SECRETAMOUNT" not in logged and "SECRETNAME" not in logged
    assert "NumericValueOutOfRange" in logged  # the class name is the one thing logged


async def test_refused_uploads_are_audited_with_a_reason_code_only(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    await upload(owner.client, b"MZ not a pdf", "SECRETNAME.pdf")
    await upload(owner.client, b"MZ again", "SECRETNAME.pdf")  # same reason: one row a minute
    await upload(owner.client, pdf_bytes(NFCU_CARD), "s.pdf", "text/plain")
    await owner.client.post("/api/imports/statements", json={})
    rows = await audit_rows(app, hid, "import.upload_refused")
    assert sorted(r.detail["reason"] for r in rows) == [
        "bad_content_type",
        "not_multipart",
        "not_pdf",
    ]
    assert all(
        r.detail["outcome"] == "failure" and set(r.detail) == {"reason", "outcome"} for r in rows
    )
    assert "SECRETNAME" not in str([r.detail for r in rows])


async def test_extreme_cursor_timestamp_is_422_not_500(owner: Owner) -> None:
    import base64
    import json

    for stamp in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"):
        raw = json.dumps([stamp, str(uuid.uuid4())]).encode()
        cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        r = await owner.client.get("/api/imports/runs", params={"cursor": cursor})
        assert r.status_code in (200, 422), r.text
    raw = json.dumps(["0001-01-01T00:00:00+14:00", str(uuid.uuid4())]).encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    assert (
        await owner.client.get("/api/imports/runs", params={"cursor": cursor})
    ).status_code == 422


async def test_missing_balances_are_null_not_zero(owner: Owner) -> None:
    text = "\n".join(line for line in NFCU_CARD.splitlines() if "Previous Balance" not in line)
    r = await upload(owner.client, pdf_bytes(text))
    assert r.status_code == 200, r.text
    (block,) = r.json()["blocks"]
    assert block["reconcile_status"] == "needs_review"
    assert block["reason"] == "missing_balance"
    assert block["opening_balance"] is None and block["closing_balance"] is None
    assert block["balance_diff"] is None
    run = (await owner.client.get("/api/imports/runs")).json()["items"][0]
    assert run["opening_balance"] is None and run["balance_diff"] is None
    assert block["txn_sum"] == "-45.00"  # the rows are real; only the balances are unknown


async def test_multi_account_upload_has_distinct_per_block_figures(owner: Owner) -> None:
    r = await upload(owner.client, pdf_bytes(DEBIT))
    assert r.status_code == 200, r.text
    first, second = r.json()["blocks"]
    assert first["run_id"] != second["run_id"] and first["account_id"] != second["account_id"]
    assert (first["opening_balance"], first["closing_balance"]) == ("0.00", "40.00")
    assert (second["opening_balance"], second["closing_balance"]) == ("1.00", "1.00")
    assert first["txn_sum"] == "40.00" and second["txn_sum"] == "0.00"
    assert first["reason"] is None


async def test_balances_only_upload_returns_what_it_read_and_writes_no_run(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    r = await upload(owner.client, pdf_bytes(SUMMARY.read_text()))
    assert r.status_code == 200, r.text
    body = r.json()
    # Current behaviour, pinned: a summary-only statement has a document but no run, so it is
    # absent from GET /runs. Showing it there needs a migration (a run without an account, or
    # balance snapshots written for it): a follow-up, not part of this story.
    assert body["balances_only"] is True and body["blocks"] == []
    assert body["balances"] and all(
        set(b) == {"account_last4", "opening_balance", "closing_balance"} for b in body["balances"]
    )
    assert any(b["closing_balance"] is not None for b in body["balances"])
    assert await count(app, hid, Document) == 1
    assert await count(app, hid, ImportRun) == 0
    assert (await owner.client.get("/api/imports/runs")).json()["items"] == []


async def test_a_private_account_of_another_person_gets_an_honest_409(
    app: FastAPI, owner: Owner
) -> None:
    from app.db_context import apply_context, unscoped_session
    from app.models import AppUser, Person
    from app.services import sessions

    hid = uuid.UUID(owner.household["id"])
    assert (await upload(owner.client, pdf_bytes(NFCU_CARD))).status_code == 200
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
    async with make_client(app) as sam:
        sam.cookies.set("hf_session", token)
        r = await upload(sam, pdf_bytes(OTHER_CARD))
    assert r.status_code == 409
    assert "belongs to another person" in r.json()["error"]["message"]
    assert "already imported" not in r.json()["error"]["message"]
