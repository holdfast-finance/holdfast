"""CSV and OFX/QFX uploads through the one import path (I2b).

Everything is synthetic: invented payees, account ids like ``EXAMPLE0000``, round amounts.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.db_context import scoped_session
from app.domain.csv_parse import csv_profile_from_mapping
from app.models import Account, AuditLog, Document, ImportRun, Transaction
from app.services import imports
from tests.api.conftest import Owner
from tests.api.test_imports import (
    count,
    member_client,
    other_household,
    pdf_bytes,
    stored_files,
)
from tests.importers.test_statement_profiles import DEBIT

HEADER = "Example Posting Date,Example Payee,Example Net Amount,Example Running Balance\n"
CSV_A = HEADER + (
    "2026-03-01,Example Deposit,100.00,100.00\n"
    "2026-03-02,Example Coffee,-4.50,95.50\n"
    "2026-03-02,Example Coffee,-4.50,91.00\n"
)
# The same account re-exported later: all of A plus one newer row.
CSV_B = CSV_A + "2026-03-03,Example Rent,-50.00,41.00\n"

OFX_HEAD = "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nENCODING:USASCII\nCHARSET:1252\n\n"


def ofx(txns: str, closing: str = "9.50", acct: str = "EXAMPLE0000", card: bool = False) -> bytes:
    stmt, frm = ("CCSTMTRS", "CCACCTFROM") if card else ("STMTRS", "BANKACCTFROM")
    return (
        f"{OFX_HEAD}<OFX><BANKMSGSRSV1><STMTTRNRS><{stmt}><CURDEF>USD\n"
        f"<{frm}><ACCTID>{acct}</{frm}>\n"
        f"<BANKTRANLIST><DTSTART>20260101<DTEND>20260131\n{txns}</BANKTRANLIST>\n"
        f"<LEDGERBAL><BALAMT>{closing}<DTASOF>20260131</LEDGERBAL>\n"
        f"</{stmt}></STMTTRNRS></BANKMSGSRSV1></OFX>"
    ).encode("cp1252")


TXNS_WITH_BALANCES = (
    "<STMTTRN><DTPOSTED>20260102<TRNAMT>-3.00<FITID>example-1<NAME>Example Books"
    "<BALAMT>7.00</STMTTRN>\n"
    "<STMTTRN><DTPOSTED>20260103<TRNAMT>2.50<FITID>example-2<NAME>Example Refund"
    "<BALAMT>9.50</STMTTRN>\n"
)
TXNS_NO_BALANCES = TXNS_WITH_BALANCES.replace("<BALAMT>7.00", "").replace("<BALAMT>9.50", "")
OFX_GOOD = ofx(TXNS_WITH_BALANCES)


@pytest.fixture(autouse=True)
def store(app: FastAPI, tmp_path: Path) -> Path:
    app.state.settings.document_store = tmp_path / "docs"
    return tmp_path / "docs"


async def up(
    client: httpx.AsyncClient,
    data: bytes,
    name: str = "export.csv",
    content_type: str = "text/csv",
    **fields: str,
) -> httpx.Response:
    """Upload as a new bank account unless the caller names another target."""
    form = {"account": "new", "account_kind": "bank", **fields}
    return await client.post(
        "/api/imports/statements", files={"file": (name, data, content_type)}, data=form
    )


async def amounts(app: FastAPI, hid: uuid.UUID) -> list[Decimal]:
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        rows = (await s.execute(select(Transaction.amount).order_by(Transaction.posted_date))).all()
    return sorted(r[0] for r in rows)


# --------------------------------------------------------------------------- CSV


async def test_csv_with_running_balance_reconciles_and_keeps_the_sign(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    r = await up(owner.client, CSV_A.encode(), "March.csv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["file_kind"], body["created"], body["filename"]) == ("csv", True, "March.csv")
    assert body["notice"] is None  # nothing imported before: no overlap to warn about
    (block,) = body["blocks"]
    assert block["reconcile_status"] == "reconciled"
    assert (block["rows_parsed"], block["rows_inserted"], block["rows_duplicate"]) == (3, 3, 0)
    assert (block["opening_balance"], block["closing_balance"]) == ("0.00", "91.00")
    assert block["txn_sum"] == "91.00"
    assert block["reason"] is None
    hid = uuid.UUID(owner.household["id"])
    assert await amounts(app, hid) == [Decimal("-4.50"), Decimal("-4.50"), Decimal("100.00")]
    (path,) = stored_files(store)
    assert path.suffix == ".csv" and len(path.stem) == 64
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        doc = (await s.execute(select(Document))).scalar_one()
        audit = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "import.upload")))
            .scalars()
            .all()
        )
    assert (doc.mime, doc.detected_profile) == ("text/csv", "csv-generic")
    assert audit[0].detail["kind"] == "csv"


async def test_csv_without_balances_is_never_reconciled_and_says_why(
    app: FastAPI, owner: Owner, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = csv_profile_from_mapping(
        {
            "date": "When",
            "description": "What",
            "amount": "Value",
            "date_format": "%Y-%m-%d",
        }
    )
    monkeypatch.setattr(imports, "load_csv_profiles", lambda: [("csv-nobalance", profile)])
    data = b"When,What,Value\n2026-03-01,Example Shop,-12.00\n2026-03-02,Example Pay,30.00\n"
    r = await up(owner.client, data)
    assert r.status_code == 200, r.text
    (block,) = r.json()["blocks"]
    assert block["reconcile_status"] == "needs_review"
    assert block["reason"] == "missing_balance"
    assert block["closing_balance"] is None
    assert block["txn_sum"] == "18.00"
    runs = (await owner.client.get("/api/imports/runs")).json()["items"]
    assert (runs[0]["reconcile_status"], runs[0]["reason"]) == ("needs_review", "missing_balance")


async def test_replaying_a_csv_adds_zero_rows(app: FastAPI, owner: Owner, store: Path) -> None:
    hid = uuid.UUID(owner.household["id"])
    first = (await up(owner.client, CSV_A.encode())).json()
    again = await up(owner.client, CSV_A.encode(), "renamed.csv")
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["created"] is False
    assert body["document_id"] == first["document_id"]
    assert [b["created"] for b in body["blocks"]] == [False]
    assert body["blocks"][0]["rows_inserted"] == 0
    assert body["blocks"][0]["run_id"] == first["blocks"][0]["run_id"]
    assert await count(app, hid, Transaction) == 3
    assert await count(app, hid, ImportRun) == 1
    assert len(stored_files(store)) == 1


async def test_an_overlapping_csv_adds_only_new_rows_and_still_reconciles(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    first = await up(owner.client, CSV_A.encode(), "a.csv")
    # The re-export goes into the account the first file made: that is what dedupes it.
    account = first.json()["blocks"][0]["account_id"]
    r = await up(owner.client, CSV_B.encode(), "b.csv", account=account)
    assert r.status_code == 200, r.text
    (block,) = r.json()["blocks"]
    assert r.json()["notice"] is None
    # The same-day twin coffees keep distinct ordinals, so neither is lost or doubled.
    assert (block["rows_parsed"], block["rows_inserted"], block["rows_duplicate"]) == (4, 1, 3)
    assert block["reconcile_status"] == "reconciled"
    assert await count(app, hid, Transaction) == 4
    assert await count(app, hid, Account) == 1


async def test_a_csv_no_profile_matches_is_a_clean_422(app: FastAPI, owner: Owner) -> None:
    r = await up(owner.client, b"Date,Memo,Sum\n2026-01-01,SECRETPAYEE,-1.00\n")
    assert r.status_code == 422
    assert "SECRETPAYEE" not in r.text
    assert await count(app, uuid.UUID(owner.household["id"]), Document) == 0


async def test_a_malformed_csv_row_is_refused_without_echoing_the_file(
    app: FastAPI, owner: Owner, caplog: pytest.LogCaptureFixture, capfd: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level("DEBUG")
    bad = HEADER + "2026-03-01,SECRETPAYEE,not-a-number,1.00\n"
    r = await up(owner.client, bad.encode())
    assert r.status_code == 422
    out = capfd.readouterr()
    assert "SECRETPAYEE" not in r.text + caplog.text + out.out + out.err
    assert await count(app, uuid.UUID(owner.household["id"]), Transaction) == 0


# --------------------------------------------------------------------------- OFX


async def test_ofx_that_reconciles(app: FastAPI, owner: Owner, store: Path) -> None:
    r = await up(owner.client, OFX_GOOD, "bank.qfx", "application/vnd.intu.qfx")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["file_kind"], body["filename"]) == ("ofx", "bank.ofx")
    (block,) = body["blocks"]
    assert block["reconcile_status"] == "reconciled"
    assert (block["opening_balance"], block["closing_balance"]) == ("10.00", "9.50")
    assert block["account_name"] == "OFX account 0000"
    assert (block["period_start"], block["period_end"]) == ("2026-01-01", "2026-01-31")
    hid = uuid.UUID(owner.household["id"])
    assert await amounts(app, hid) == [Decimal("-3.00"), Decimal("2.50")]
    (path,) = stored_files(store)
    assert path.suffix == ".ofx"


async def test_ofx_that_does_not_reconcile_stores_a_reason(app: FastAPI, owner: Owner) -> None:
    # A ledger balance that disagrees with the running balances.
    r = await up(owner.client, ofx(TXNS_WITH_BALANCES, closing="99.50"), "x.ofx", "")
    assert r.status_code == 200, r.text
    (block,) = r.json()["blocks"]
    assert block["reconcile_status"] == "needs_review"
    assert block["reason"] == "balance_mismatch"
    # No per-transaction balances: the opening balance cannot be established.
    r = await up(owner.client, ofx(TXNS_NO_BALANCES, acct="EXAMPLE1111"), "y.ofx", "")
    (block,) = r.json()["blocks"]
    assert (block["reconcile_status"], block["reason"]) == ("needs_review", "missing_balance")
    runs = (await owner.client.get("/api/imports/runs")).json()["items"]
    assert {r["reason"] for r in runs} == {"balance_mismatch", "missing_balance"}


async def test_replaying_an_ofx_adds_zero_rows(app: FastAPI, owner: Owner) -> None:
    hid = uuid.UUID(owner.household["id"])
    await up(owner.client, OFX_GOOD, "a.ofx", "")
    again = await up(owner.client, OFX_GOOD, "a.ofx", "")
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert await count(app, hid, Transaction) == 2
    assert await count(app, hid, ImportRun) == 1


async def test_an_ofx_card_statement_becomes_a_credit_account_with_owed_negative(
    app: FastAPI, owner: Owner
) -> None:
    txns = (
        "<STMTTRN><DTPOSTED>20260105<TRNAMT>-20.00<FITID>c1<NAME>Example Store"
        "<BALAMT>-132.00</STMTTRN>\n"
        "<STMTTRN><DTPOSTED>20260106<TRNAMT>-5.00<FITID>c2<NAME>Example Cafe"
        "<BALAMT>-137.00</STMTTRN>\n"
    )
    r = await up(
        owner.client, ofx(txns, closing="-137.00", acct="EXAMPLECARD1", card=True), "c.ofx"
    )
    assert r.status_code == 200, r.text
    (block,) = r.json()["blocks"]
    assert (block["opening_balance"], block["closing_balance"]) == ("-112.00", "-137.00")
    assert block["reconcile_status"] == "reconciled"
    hid = uuid.UUID(owner.household["id"])
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        assert (await s.execute(select(Account.type))).scalar_one() == "credit"


async def test_malformed_and_unsupported_ofx_are_refused_content_free(
    app: FastAPI, owner: Owner, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    broken = (OFX_HEAD + "<OFX><BANKMSGSRSV1><STMTRS>SECRETBODY<STMTTRN>").encode()
    r = await up(owner.client, broken, "bad.ofx", "")
    assert r.status_code == 422
    assert "SECRETBODY" not in r.text + caplog.text
    cad = OFX_GOOD.replace(b"<CURDEF>USD", b"<CURDEF>CAD")
    assert (await up(owner.client, cad, "cad.ofx", "")).status_code == 422
    assert await count(app, uuid.UUID(owner.household["id"]), Transaction) == 0


# --------------------------------------------------------------------------- guards


async def test_the_bytes_decide_the_kind_not_the_name_or_mime_type(owner: Owner) -> None:
    # A real CSV named .pdf with a PDF mime type is a CSV; a PDF named .csv is a PDF.
    r = await up(owner.client, CSV_A.encode(), "statement.pdf", "application/pdf")
    assert (r.status_code, r.json()["file_kind"], r.json()["filename"]) == (
        200,
        "csv",
        "statement.csv",
    )
    p = await up(owner.client, pdf_bytes(DEBIT), "export.csv", "text/csv")
    assert p.status_code == 200, p.text
    assert p.json()["file_kind"] == "pdf"


async def test_binary_files_posing_as_csv_are_refused_and_store_nothing(
    app: FastAPI, owner: Owner, store: Path
) -> None:
    for blob in (
        b"MZ\x90\x00\x03,,,,\x00\x00",
        b"\x89PNG\r\n\x1a\n,,,\x00",
        # Clean first chunk, binary after it.
        CSV_A.encode() + b"," * 70_000 + b"\x00\x01\x02",
    ):
        r = await up(owner.client, blob, "data.csv")
        assert r.status_code == 422, r.text
    wrong_mime = await up(owner.client, CSV_A.encode(), "a.csv", "image/png")
    assert wrong_mime.status_code == 422
    assert (await up(owner.client, b"", "empty.csv")).status_code == 422
    assert stored_files(store) == []
    assert await count(app, uuid.UUID(owner.household["id"]), Document) == 0


async def test_an_oversized_text_file_is_refused_by_real_byte_count(
    owner: Owner, store: Path
) -> None:
    big = HEADER.encode() + b"2026-03-01,Example,1.00,1.00\n" * (imports.MAX_TEXT_BYTES // 28 + 10)
    r = await up(owner.client, big)
    assert r.status_code == 413
    assert "10 MiB" in r.json()["error"]["message"]
    assert stored_files(store) == []


async def test_refusals_are_audited_by_reason_code_only(app: FastAPI, owner: Owner) -> None:
    await up(owner.client, b"MZ\x00SECRETNAME", "SECRETNAME.csv")
    hid = uuid.UUID(owner.household["id"])
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "import.upload_refused")))
            .scalars()
            .all()
        )
    assert [r.detail["reason"] for r in rows] == ["unsupported_file"]
    assert "SECRETNAME" not in str(rows[0].detail)


# --------------------------------------------------------------------------- overlap and tenancy


OVERLAP = (
    HEADER
    + "2019-03-04,Example Deposit One,800.00,800.00\n"
    + "2019-03-07,Example Withdrawal One,-20.00,780.00\n"
)


async def test_a_csv_into_a_new_account_warns_that_it_may_repeat_pdf_rows(
    app: FastAPI, owner: Owner
) -> None:
    """Choosing "new" next to a PDF's account keeps the two apart and says so."""
    hid = uuid.UUID(owner.household["id"])
    pdf = await up(owner.client, pdf_bytes(DEBIT), "debit.pdf", "application/pdf")
    assert pdf.status_code == 200, pdf.text
    pdf_rows = await count(app, hid, Transaction)
    r = await up(owner.client, OVERLAP.encode())
    assert r.status_code == 200, r.text
    (block,) = r.json()["blocks"]
    assert (block["rows_inserted"], block["rows_duplicate"]) == (2, 0)
    assert "own new account" in r.json()["notice"]
    assert "repeat transactions" in r.json()["notice"]
    assert await count(app, hid, Transaction) == pdf_rows + 2
    # Replaying either file still adds nothing.
    again = await up(owner.client, OVERLAP.encode())
    assert again.json()["notice"] is None
    await up(owner.client, pdf_bytes(DEBIT), "debit.pdf", "application/pdf")
    assert await count(app, hid, Transaction) == pdf_rows + 2


async def test_a_csv_can_be_added_to_the_matching_pdf_account_without_double_counting(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    await up(owner.client, pdf_bytes(DEBIT), "debit.pdf", "application/pdf")
    pdf_rows = await count(app, hid, Transaction)
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        target = (
            await s.execute(
                select(Transaction.account_id).where(
                    Transaction.description_raw.like("Example Deposit One%")
                )
            )
        ).scalar_one()
    listed = (await owner.client.get("/api/imports/accounts")).json()
    assert str(target) in {a["id"] for a in listed}
    r = await up(owner.client, OVERLAP.encode(), account=str(target))
    assert r.status_code == 200, r.text
    (block,) = r.json()["blocks"]
    assert block["account_id"] == str(target)
    assert (block["rows_inserted"], block["rows_duplicate"]) == (0, 2)
    assert block["reconcile_status"] == "reconciled"
    assert r.json()["notice"] is None
    assert await count(app, hid, Transaction) == pdf_rows
    # The same bytes sent to a different account are not imported a second time.
    other = await up(owner.client, OVERLAP.encode())
    assert other.json()["created"] is False
    assert await count(app, hid, Transaction) == pdf_rows


# --------------------------------------------------------------------------- targets (Bulwark H1)


async def test_two_people_uploading_the_same_profile_get_separate_accounts(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    mine = await up(owner.client, CSV_A.encode())
    assert mine.status_code == 200, mine.text
    async with await member_client(app, hid) as sam:
        theirs = await up(sam, CSV_B.encode())
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["blocks"][0]["account_id"] != mine.json()["blocks"][0]["account_id"]
        # Sam cannot see, or write into, the owner's account: one answer for both.
        assert (await sam.get("/api/imports/accounts")).json()[0]["id"] == (
            theirs.json()["blocks"][0]["account_id"]
        )
        into = await up(
            sam,
            CSV_B.replace("Rent", "Gas").encode(),
            account=mine.json()["blocks"][0]["account_id"],
        )
        ghost = await up(sam, CSV_B.replace("Rent", "Oil").encode(), account=str(uuid.uuid4()))
    assert into.status_code == ghost.status_code == 409
    assert into.json() == ghost.json()
    assert await count(app, hid, Account) == 2
    assert await count(app, hid, Transaction) == 3 + 4


async def test_one_person_importing_checking_then_card_keeps_equal_rows_apart(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    checking = await up(owner.client, CSV_A.encode(), account_kind="bank")
    # The card export holds the same dated rows plus one: same fingerprints, other account.
    card = await up(owner.client, CSV_B.encode(), account_kind="card")
    assert card.status_code == 200, card.text
    (block,) = card.json()["blocks"]
    assert (block["rows_inserted"], block["rows_duplicate"]) == (4, 0)
    assert block["reconcile_status"] == "reconciled"
    assert block["account_id"] != checking.json()["blocks"][0]["account_id"]
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        types = sorted((await s.execute(select(Account.type))).scalars().all())
    assert types == ["credit", "depository"]
    assert await count(app, hid, Transaction) == 3 + 4


async def test_a_csv_must_name_its_account_and_a_new_one_its_kind(owner: Owner) -> None:
    bare = await owner.client.post(
        "/api/imports/statements", files={"file": ("a.csv", CSV_A.encode(), "text/csv")}
    )
    assert bare.status_code == 422
    nokind = await owner.client.post(
        "/api/imports/statements",
        files={"file": ("a.csv", CSV_A.encode(), "text/csv")},
        data={"account": "new"},
    )
    assert nokind.status_code == 422
    junk = await up(owner.client, CSV_A.encode(), account="not-a-uuid")
    assert junk.status_code == 422


async def test_a_principal_with_no_person_must_choose_a_joint_account(
    app: FastAPI, owner: Owner
) -> None:
    from types import SimpleNamespace

    from app.importers.export_statement import csv_statement
    from app.importers.profile_loader import load_csv_profiles
    from app.services.imports import ImportService, UploadFields, UploadRefused
    from app.services.statement_import import (
        AccountTarget,
        AccountTargetError,
        DocumentMeta,
        import_statement,
    )

    service = ImportService(None, SimpleNamespace(person_id=None))  # type: ignore[arg-type]
    with pytest.raises(UploadRefused) as refused:
        service._target("csv", UploadFields(account="new", account_kind="bank"), "a" * 64)
    assert refused.value.reason == "joint_not_chosen"
    chosen = service._target("csv", UploadFields("new", "bank", "1"), "a" * 64)
    assert chosen is not None and chosen.joint

    hid = uuid.UUID(owner.household["id"])
    statement = csv_statement(CSV_A.encode(), load_csv_profiles())
    assert statement is not None
    meta = DocumentMeta("b" * 64, "a.csv", f"statements/{hid}/{'b' * 64}.csv", 10, "text/csv")
    factory = app.state.session_factory
    with pytest.raises(AccountTargetError):  # nothing chosen: no joint account by default
        await import_statement(
            factory, hid, statement, meta, person_id=None, target=AccountTarget()
        )
    done = await import_statement(
        factory,
        hid,
        statement,
        meta,
        person_id=None,
        target=AccountTarget(joint=True, new_token="t"),
    )
    assert done.rows_inserted == 3
    async with scoped_session(factory, hid, None, household_wide=True) as s:
        acct = (await s.execute(select(Account))).scalar_one()
    assert (acct.owner_person_id, acct.visibility) == (None, "shared")


async def test_ofx_bank_and_card_with_the_same_last_four_are_different_accounts(
    app: FastAPI, owner: Owner
) -> None:
    hid = uuid.UUID(owner.household["id"])
    card_txns = (
        "<STMTTRN><DTPOSTED>20260102<TRNAMT>-3.00<FITID>k1<NAME>Example Books"
        "<BALAMT>-7.00</STMTTRN>\n"
    )
    bank = await up(owner.client, ofx(TXNS_WITH_BALANCES, acct="BANK-AAAA-0000"), "b.ofx", "")
    card = await up(
        owner.client, ofx(card_txns, closing="-7.00", acct="CARD-BBBB-0000", card=True), "c.ofx", ""
    )
    bank2 = await up(
        owner.client, ofx(TXNS_WITH_BALANCES, acct="BANK-CCCC-0000", closing="9.50"), "d.ofx", ""
    )
    assert (bank.status_code, card.status_code, bank2.status_code) == (200, 200, 200)
    ids = {r.json()["blocks"][0]["account_id"] for r in (bank, card, bank2)}
    assert len(ids) == 3
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        accounts = (await s.execute(select(Account))).scalars().all()
    assert sorted(a.type for a in accounts) == ["credit", "depository", "depository"]
    assert all(
        "BANK" not in a.external_account_id and "CARD" not in a.external_account_id
        for a in accounts
    )
    # A re-export of the same real account (new bytes) lands in the same account.
    again = await up(
        owner.client, ofx(TXNS_WITH_BALANCES + "\n", acct="BANK-AAAA-0000"), "e.ofx", ""
    )
    assert again.json()["blocks"][0]["account_id"] == bank.json()["blocks"][0]["account_id"]
    assert again.json()["blocks"][0]["rows_inserted"] == 0


async def test_an_ofx_into_an_account_of_the_other_kind_is_refused(owner: Owner) -> None:
    bank = await up(owner.client, OFX_GOOD, "b.ofx", "")
    target = bank.json()["blocks"][0]["account_id"]
    card_txns = (
        "<STMTTRN><DTPOSTED>20260102<TRNAMT>-3.00<FITID>k1<NAME>Example Books"
        "<BALAMT>-7.00</STMTTRN>\n"
    )
    r = await up(
        owner.client, ofx(card_txns, closing="-7.00", card=True), "c.ofx", "", account=target
    )
    assert r.status_code == 409
    assert "other kind" in r.json()["error"]["message"]


# --------------------------------------------------------------------------- OFX signs and caps


async def test_ofx_trntype_against_the_amount_sign_is_flagged(owner: Owner) -> None:
    wrong = (
        "<STMTTRN><DTPOSTED>20260102<TRNAMT>3.00<TRNTYPE>DEBIT<FITID>s1<NAME>Example Books"
        "<BALAMT>13.00</STMTTRN>\n"
        "<STMTTRN><DTPOSTED>20260103<TRNAMT>-2.50<TRNTYPE>CREDIT<FITID>s2<NAME>Example Refund"
        "<BALAMT>10.50</STMTTRN>\n"
    )
    r = await up(owner.client, ofx(wrong, closing="10.50"), "s.ofx", "")
    (block,) = r.json()["blocks"]
    assert block["reconcile_status"] == "needs_review"
    assert "sign_conflict" in block["reason"]
    ok = TXNS_WITH_BALANCES.replace("<TRNAMT>-3.00", "<TRNAMT>-3.00<TRNTYPE>DEBIT")
    r = await up(owner.client, ofx(ok, acct="EXAMPLE2222"), "ok.ofx", "")
    assert r.json()["blocks"][0]["reconcile_status"] == "reconciled"
    # On a card a PAYMENT is money in, not a debit.
    pay = (
        "<STMTTRN><DTPOSTED>20260105<TRNAMT>20.00<TRNTYPE>PAYMENT<FITID>p1<NAME>Example Thanks"
        "<BALAMT>-80.00</STMTTRN>\n"
        "<STMTTRN><DTPOSTED>20260106<TRNAMT>-5.00<TRNTYPE>POS<FITID>p2<NAME>Example Cafe"
        "<BALAMT>-85.00</STMTTRN>\n"
    )
    r = await up(
        owner.client, ofx(pay, closing="-85.00", acct="EXAMPLE3333", card=True), "p.ofx", ""
    )
    assert r.json()["blocks"][0]["reconcile_status"] == "reconciled"


async def test_ofx_over_its_own_byte_cap_is_refused(owner: Owner, store: Path) -> None:
    big = ofx(TXNS_WITH_BALANCES) + b" " * (imports.MAX_OFX_BYTES + 1)
    r = await up(owner.client, big, "big.ofx", "")
    assert r.status_code == 413
    assert "5 MiB" in r.json()["error"]["message"]
    assert stored_files(store) == []


async def test_a_missing_import_id_key_refuses_an_ofx_and_audits_it(
    app: FastAPI, owner: Owner
) -> None:
    app.state.settings.import_id_key = None
    r = await up(owner.client, OFX_GOOD, "a.ofx", "")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "server_error"
    hid = uuid.UUID(owner.household["id"])
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "import.upload_refused")))
            .scalars()
            .all()
        )
    assert [r.detail["reason"] for r in rows] == ["id_key_missing"]


async def test_utf16_csv_is_accepted_but_not_with_control_characters(owner: Owner) -> None:
    good = await up(owner.client, CSV_A.encode("utf-16"))
    assert good.status_code == 200, good.text
    assert good.json()["blocks"][0]["rows_inserted"] == 3
    bad = await up(owner.client, CSV_B.replace("Rent", "Re\x01nt").encode("utf-16"), "b.csv")
    assert bad.status_code == 422
    # A NUL byte hidden after the first chunk of a UTF-16 file is still caught.
    late = CSV_B.replace("Rent", "Gas").encode("utf-16") + ("x" * 70_000 + "\x00").encode(
        "utf-16-le"
    )
    assert (await up(owner.client, late, "c.csv")).status_code == 422
    truncated = CSV_B.replace("Rent", "Oil").encode("utf-16") + b"\x41"
    assert (await up(owner.client, truncated, "d.csv")).status_code == 422


async def test_the_duplicate_lookup_stays_under_the_database_parameter_limit(
    app: FastAPI, owner: Owner
) -> None:
    from app.services.statement_import import _unexplained_duplicates

    hid = uuid.UUID(owner.household["id"])
    run = ImportRun(period_start=None, period_end=None)
    async with scoped_session(app.state.session_factory, hid, None, household_wide=True) as s:
        prints = [f"{i:064x}" for i in range(40_000)]  # more than asyncpg's 32767 limit
        missing = await _unexplained_duplicates(s, uuid.uuid4(), uuid.uuid4(), run, prints)
    assert missing == 40_000


async def test_another_household_cannot_see_or_collide_with_a_csv_import(
    app: FastAPI, owner: Owner
) -> None:
    first = (await up(owner.client, CSV_A.encode())).json()
    other = await other_household(app)
    assert (await other.get("/api/imports/runs")).json()["items"] == []
    assert (
        await other.post(f"/api/imports/runs/{first['blocks'][0]['run_id']}/accept")
    ).status_code in (403, 404)
    # The same bytes in another household are their own document and rows (sha is per household).
    mine = await up(other, CSV_A.encode())
    assert mine.status_code == 200, mine.text
    assert mine.json()["created"] is True
    assert mine.json()["document_id"] != first["document_id"]
    assert (await other.get("/api/imports/runs")).json()["items"][0]["document_id"] == mine.json()[
        "document_id"
    ]
