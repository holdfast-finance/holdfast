"""Statement sanitizer tests. Everything here is synthetic: invented people, numbers, merchants."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import stat
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pytest
from reportlab.pdfgen import canvas

from app.importers import sanitize as sz

NAME = "Jonathan Quillfeather"
STREET = "742 Evergreen Terrace"
CARD = "4111111111111111"
ROUTING = "021000021"
EMAIL = "jq.quillfeather@mailbox.example"
PHONE = "555-867-5309"
SECRETS = [
    "Jonathan",
    "Quillfeather",
    "Evergreen",
    "Terrace",
    CARD,
    ROUTING,
    EMAIL,
    "5309",
    "ZEBRATRONIX",
    "Marigold",
    "42.17",
    "1,200.33",
    "250.00",
]

ROWS = [
    ("01/05/2025", "ZEBRATRONIX #4412", "-42.17"),
    ("01/09/2025", "Marigold Hardware", "-7.50"),
    ("01/15/2025", "Payroll Deposit", "250.00"),
]
OPENING, CLOSING = "1,000.00", "1,200.33"


def make_pdf(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=(612, 792))
    y = 740
    for text in (
        "First Example Bank",
        NAME,
        STREET,
        f"Account {CARD}  Routing {ROUTING}",
        f"{EMAIL}  {PHONE}",
        "Statement Period 01/01/2025 - 01/31/2025",
    ):
        c.drawString(50, y, text)
        y -= 16
    c.drawString(50, y, "Beginning Balance")
    c.drawRightString(560, y, OPENING)
    y -= 24
    for d, desc, amt in ROWS:
        c.drawString(50, y, d)
        c.drawString(130, y, desc)
        c.drawRightString(560, y, amt)
        y -= 16
    c.drawString(50, y, "Ending Balance")
    c.drawRightString(560, y, CLOSING)
    c.showPage()
    c.drawString(50, 740, "Page 2 of 2")
    c.save()


def leaks(secret: str, blob: str) -> bool:
    """True when secret appears as its own token (not as a digit-run inside a longer number)."""
    return re.search(rf"(?<![\d,.]){re.escape(secret)}(?![\d])", blob) is not None


def run(src: Path, out: Path) -> dict[str, Any]:
    summary, _ = sz.sanitize_path(src, out, seed=7)
    return summary


def all_output(out: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(out.iterdir()))


def pdf_dir(tmp_path: Path, name: str = "statement.pdf") -> tuple[Path, Path]:
    src = tmp_path / "in"
    src.mkdir()
    make_pdf(src / name)
    return src, tmp_path / "out"


def test_pdf_has_no_original_sensitive_token_and_keeps_structure(tmp_path: Path) -> None:
    src, out = pdf_dir(tmp_path)
    summary = run(src, out)
    blob = all_output(out)
    for secret in SECRETS:
        assert not leaks(secret, blob)
    assert "statement.pdf" not in blob
    txt = (out / "doc-0001.txt").read_text()
    assert "Beginning Balance" in txt and "Ending Balance" in txt
    assert "\f" in txt
    assert len(re.findall(r"\b\d{2}/\d{2}/\d{4}\b", txt)) >= 5
    assert summary["documents"] == 1 and summary["failed"] == 0
    report = json.loads((out / "doc-0001.report.json").read_text())
    assert report["replaced"]["amount"] >= 5
    assert report["replaced"]["date"] >= 5


def test_reconciliation_survives_substitution(tmp_path: Path) -> None:
    src, out = pdf_dir(tmp_path)
    rep = run(src, out)["docs"][0]
    assert rep["reconciled_before"] is True
    assert rep["reconciled_after"] is True
    assert rep["reconciliation_survived"] is True
    amounts = [sz._signed(t) for t in (out / "doc-0001.txt").read_text().split()]
    assert Decimal("42.17") not in [abs(a) for a in amounts if a is not None]


def test_dates_keep_order_and_format_but_not_the_original_gaps() -> None:
    s = sz.Sanitizer(seed=3)
    texts = ["01/05/2025", "01/15/2025", "01/20/2025"]
    out = s.tokens(texts)
    parsed = [datetime.strptime(item, "%m/%d/%Y") for item in out]
    original = [datetime.strptime(item, "%m/%d/%Y") for item in texts]
    gaps_out = [(parsed[i + 1] - parsed[i]).days for i in range(2)]
    gaps_in = [(original[i + 1] - original[i]).days for i in range(2)]
    assert gaps_out != gaps_in
    assert all(gap > 0 for gap in gaps_out)
    assert out[0] != texts[0]
    iso = s.token("2025-01-05")
    assert iso[4] == "-"
    assert iso != "2025-01-05"
    assert s.tokens(["Jan", "5,", "2025"]) != ["Jan", "5,", "2025"]


def test_csv_default_deny_and_amount_format(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.csv").write_text(
        "Date,Description,Amount,Balance\n"
        f"2025-01-05,ZEBRATRONIX {PHONE},-42.17,957.83\n"
        f"2025-01-09,{NAME} {EMAIL},1200.50,2158.33\n"
    )
    out = tmp_path / "out"
    run(src, out)
    blob = (out / "doc-0001.txt").read_text()
    for secret in (*SECRETS[:4], EMAIL, PHONE, "ZEBRATRONIX", "42.17", "957.83", "1200.50"):
        assert not leaks(secret, blob)
    assert blob.splitlines()[0] == "Date,Description,Amount,Balance"
    assert "example.test" in blob
    rows = [ln.split(",") for ln in blob.splitlines()[1:]]
    assert all(r[2].count(".") == 1 and len(r[2].split(".")[1]) == 2 for r in rows)
    assert rows[0][2].startswith("-")


def test_ofx_is_sanitized_but_keeps_tags(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "x.qfx").write_text(
        "OFXHEADER:100\nDATA:OFXSGML\n<OFX>\n<BANKID>021000021\n<ACCTID>00123456789\n"
        "<DTPOSTED>20250105120000\n<TRNTYPE>DEBIT\n<TRNAMT>-42.17\n<NAME>ZEBRATRONIX\n"
        "<MEMO>Jonathan Quillfeather\n<BALAMT>957.83\n</OFX>\n"
    )
    out = tmp_path / "out"
    run(src, out)
    blob = (out / "doc-0001.txt").read_text()
    for secret in ("021000021", "00123456789", "20250105", "-42.17", "ZEBRATRONIX", "Jonathan"):
        assert not leaks(secret, blob)
    assert "957.83" not in blob
    assert "<TRNTYPE>DEBIT" in blob and "OFXHEADER:100" in blob
    posted = blob.split("<DTPOSTED>")[1].split("\n")[0]
    assert len(posted) == 14
    assert not posted.endswith("120000")


def test_output_permissions(tmp_path: Path) -> None:
    src, out = pdf_dir(tmp_path)
    run(src, out)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    for p in out.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def make_zip(path: Path, members: list[tuple[str, bytes, int | None]]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data, attr in members:
            zi = zipfile.ZipInfo(name)
            if attr is not None:
                zi.external_attr = attr << 16
            zf.writestr(zi, data)


def test_zip_of_statements_is_processed(tmp_path: Path) -> None:
    z = tmp_path / "in.zip"
    make_zip(z, [("bank/jan.csv", b"Date,Amount\n2025-01-05,-1.00\n", None), ("n.txt", b"x", None)])
    assert run(z, tmp_path / "out")["documents"] == 1


def test_zip_slip_rejected(tmp_path: Path) -> None:
    z = tmp_path / "evil.zip"
    make_zip(z, [("../../escape.csv", b"a,b\n", None)])
    with pytest.raises(sz.SanitizeError, match="unsafe"):
        run(z, tmp_path / "out")


@pytest.mark.parametrize("bad", ["/etc/x.csv", "a\\..\\b.csv", "C:/x.csv"])
def test_zip_absolute_and_backslash_rejected(tmp_path: Path, bad: str) -> None:
    z = tmp_path / "e.zip"
    make_zip(z, [(bad, b"a\n", None)])
    with pytest.raises(sz.SanitizeError):
        run(z, tmp_path / "out")


def test_zip_symlink_rejected(tmp_path: Path) -> None:
    z = tmp_path / "l.zip"
    make_zip(z, [("link.csv", b"/etc/passwd", 0o120777)])
    with pytest.raises(sz.SanitizeError, match="symlink"):
        run(z, tmp_path / "out")


def test_oversized_and_too_many_zip_rejected(tmp_path: Path) -> None:
    z = tmp_path / "big.zip"
    make_zip(z, [("a.csv", b"x" * 5000, None)])
    with pytest.raises(sz.SanitizeError, match="size"):
        sz.sanitize_path(z, tmp_path / "o1", limits=sz.Limits(max_file_bytes=1000))
    with pytest.raises(sz.SanitizeError, match="uncompressed"):
        sz.sanitize_path(z, tmp_path / "o2", limits=sz.Limits(max_total_bytes=1000))
    many = tmp_path / "many.zip"
    make_zip(many, [(f"{i}.csv", b"a\n", None) for i in range(5)])
    with pytest.raises(sz.SanitizeError, match="many"):
        sz.sanitize_path(many, tmp_path / "o3", limits=sz.Limits(max_files=3))


def test_symlink_in_input_dir_rejected(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (tmp_path / "t.csv").write_text("a\n")
    os.symlink(tmp_path / "t.csv", src / "l.csv")
    with pytest.raises(sz.SanitizeError, match="symlink"):
        run(src, tmp_path / "out")


def test_image_only_pdf_is_flagged_for_ocr(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    c = canvas.Canvas(str(src / "scan.pdf"))
    c.showPage()
    c.save()
    assert run(src, tmp_path / "out")["needs_ocr"] == 1


def test_canary_never_in_logs_stdout_or_stderr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    src, out = pdf_dir(tmp_path, "canary-statement.pdf")
    (src / "bad.pdf").write_bytes(b"%PDF-1.4 CANARY-" + CARD.encode() + b" garbage")
    caplog.set_level(logging.DEBUG)
    rc = sz.main([str(src), str(out)])
    seen = capsys.readouterr()
    haystack = caplog.text + seen.out + seen.err
    assert rc == 1
    for secret in (*SECRETS, "CANARY", "canary-statement", "bad.pdf"):
        assert not leaks(secret, haystack)
    assert "documents=2 failed=1" in seen.err


def test_review_prints_sanitized_only_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src, _ = pdf_dir(tmp_path)
    assert sz.main([str(src), "--review"]) == 0
    out = capsys.readouterr().out
    assert "Beginning Balance" in out
    for secret in SECRETS:
        assert not leaks(secret, out)
    assert not (tmp_path / "out").exists()


def _rows_of(path: Path) -> list[list[str]]:
    doc = sz.extract(path)
    return [[token.text for token in line.tokens] for page in doc.pages for line in page.lines]


def _layout_rows(path: Path) -> list[list[str]]:
    layout = json.loads(path.read_text())
    return [
        [token["text"] for token in line["tokens"]]
        for page in layout["pages"]
        for line in page["lines"]
    ]


def _signed_amounts(rows: list[list[str]]) -> list[Decimal]:
    found: list[Decimal] = []
    for texts in rows:
        for index, token in enumerate(texts):
            nxt = texts[index + 1] if index + 1 < len(texts) else ""
            value = sz._signed(token, nxt)
            if value is not None:
                found.append(value)
    return found


def _assert_not_a_scale(original: list[Decimal], sanitized: list[Decimal]) -> None:
    assert original and len(original) == len(sanitized)

    def spread(values: list[Decimal]) -> Decimal:
        present = [abs(item) for item in values if item != 0]
        return max(present) / min(present)

    assert not any(
        all(new == old * factor for new, old in zip(sanitized, original, strict=True))
        for factor in range(2, 31)
    )
    assert spread(sanitized) != spread(original)


def _assert_identity(rows: list[list[str]]) -> None:
    opening: Decimal | None = None
    closing: Decimal | None = None
    txns: list[Decimal] = []
    for texts in rows:
        low = " ".join(texts).lower()
        amounts: list[Decimal] = []
        for index, token in enumerate(texts):
            nxt = texts[index + 1] if index + 1 < len(texts) else ""
            value = sz._signed(token, nxt)
            if value is not None:
                amounts.append(value)
        if sz.OPEN_RE.search(low) and amounts:
            opening = amounts[-1]
            continue
        if sz.CLOSE_RE.search(low) and amounts:
            closing = amounts[-1]
            continue
        if sz._starts_with_date(texts) and amounts:
            txns.append(amounts[0] if len(amounts) == 1 else amounts[-2])
    assert opening is not None and closing is not None and txns
    assert abs(opening + sum(txns, Decimal(0)) - closing) <= Decimal("0.01")


def test_amounts_are_independent_and_still_reconcile(tmp_path: Path) -> None:
    src, out = pdf_dir(tmp_path)
    original_rows = _rows_of(src / "statement.pdf")
    run(src, out)
    sanitized_rows = _layout_rows(out / "doc-0001.layout.json")
    _assert_not_a_scale(_signed_amounts(original_rows), _signed_amounts(sanitized_rows))
    _assert_identity(sanitized_rows)

    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "a.csv").write_text(
        "Date,Description,Amount\n"
        "Beginning Balance,,1000.00\n"
        "2025-01-05,Coffee,-42.17\n"
        "2025-01-09,Hardware,-7.50\n"
        "2025-01-15,Payroll,250.00\n"
        "Ending Balance,,1200.33\n"
    )
    csv_out = tmp_path / "csv-out"
    run(csv_dir, csv_out)
    original_csv = list(csv.reader((csv_dir / "a.csv").open()))
    sanitized_csv = list(csv.reader((csv_out / "doc-0001.txt").open()))
    _assert_not_a_scale(_signed_amounts(original_csv), _signed_amounts(sanitized_csv))
    _assert_identity(sanitized_csv)

    ofx_dir = tmp_path / "ofx"
    ofx_dir.mkdir()
    (ofx_dir / "a.ofx").write_text(
        "<LEDGERBAL>\n<BALAMT>1000.00\n"
        "<STMTTRN>\n<TRNAMT>-42.17\n"
        "<STMTTRN>\n<TRNAMT>-7.50\n"
        "<STMTTRN>\n<TRNAMT>250.00\n"
        "<AVAILBAL>\n<BALAMT>1200.33\n"
    )
    ofx_out = tmp_path / "ofx-out"
    run(ofx_dir, ofx_out)
    raw_ofx = (ofx_dir / "a.ofx").read_text()
    clean_ofx = (ofx_out / "doc-0001.txt").read_text()

    def parts(text: str) -> list[Decimal]:
        bals = re.findall(r"<BALAMT>([+-]?\d+(?:\.\d+)?)", text)
        txns = re.findall(r"<TRNAMT>([+-]?\d+(?:\.\d+)?)", text)
        return [Decimal(item) for item in (*bals[:1], *txns, *bals[-1:])]

    before, after = parts(raw_ofx), parts(clean_ofx)
    _assert_not_a_scale(before, after)
    opening, txns, closing = after[0], after[1:-1], after[-1]
    assert abs(opening + sum(txns, Decimal(0)) - closing) <= Decimal("0.01")


def test_short_amounts_dates_and_house_numbers_do_not_survive() -> None:
    s = sz.Sanitizer(seed=1)
    assert s.token("$5") != "$5"
    dotted = s.token(".50")
    assert dotted != ".50" and ".50" not in dotted
    assert "1.234,56" not in s.token("1.234,56")
    ssn = s.token("000-00-0000")
    assert ssn != "000-00-0000" and "-00-" not in ssn
    glued = s.token("15-Jan-2024")
    assert glued != "15-Jan-2024"
    assert not re.search(r"(^|[-/])15([-/])", glued)
    assert "2024" not in glued
    assert s.token("12") != "12"
    assert s.text("12.00") != "12.00"
    assert "19.99" not in s.token("19.99%")


def test_surname_labels_do_not_survive() -> None:
    s = sz.Sanitizer(seed=1)
    for word in ("PRICE", "BOND", "CASH", "MILES", "GROSS", "Page", "Post"):
        assert s.token(word).lower() != word.lower()


def test_report_hashes_sanitized_text_not_the_raw_file(tmp_path: Path) -> None:
    src, out = pdf_dir(tmp_path)
    prefix = hashlib.sha256((src / "statement.pdf").read_bytes()).hexdigest()[:12]
    run(src, out)
    assert prefix not in all_output(out)
    report = json.loads((out / "doc-0001.report.json").read_text())
    text = (out / "doc-0001.txt").read_text()
    assert report["sha256_12"] == hashlib.sha256(text.encode()).hexdigest()[:12]


def test_brokerage_value_is_the_product(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "b.csv").write_text("Symbol,Quantity,Price,Value\nACME,10,15.00,150.00\n")
    out = tmp_path / "out"
    run(src, out)
    rows = list(csv.reader((out / "doc-0001.txt").open()))
    qty, price, value = (Decimal(rows[1][1]), Decimal(rows[1][2]), Decimal(rows[1][3]))
    assert qty * price == value
    _assert_not_a_scale(
        [Decimal(10), Decimal("15.00"), Decimal("150.00")],
        [qty, price, value],
    )


def test_ofx_email_does_not_keep_the_address_shape(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "x.qfx").write_text("<OFX>\n<MEMO>jq.quillfeather@mailbox.example\n</OFX>\n")
    out = tmp_path / "out"
    run(src, out)
    blob = (out / "doc-0001.txt").read_text()
    assert re.search(r"user\d{3}@example\.test", blob)
    assert "quillfeather" not in blob and "mailbox" not in blob


def test_pdf_page_cap_aborts_without_page_text(tmp_path: Path) -> None:
    assert sz.Limits.max_pdf_pages == 50
    src = tmp_path / "in"
    src.mkdir()
    c = canvas.Canvas(str(src / "many.pdf"))
    c.drawString(72, 72, "CANARYPAGE")
    c.showPage()
    c.showPage()
    c.showPage()
    c.save()
    with pytest.raises(sz.SanitizeError, match="too many pages") as caught:
        sz.sanitize_path(src, tmp_path / "out", limits=sz.Limits(max_pdf_pages=2))
    assert "CANARYPAGE" not in str(caught.value)


def _patch_uncompressed_size(archive: Path, size: int) -> None:
    """Overwrite local and central uncompressed-size fields. The payload is unchanged."""
    data = bytearray(archive.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        start = 0
        while True:
            found = data.find(signature, start)
            if found < 0:
                break
            data[found + offset : found + offset + 4] = size.to_bytes(4, "little")
            start = found + 4
    archive.write_bytes(data)


def test_zip_lying_uncompressed_size_fails_integrity(tmp_path: Path) -> None:
    """A tiny declared size cannot inflate through ZipFile.open.

    CPython stops at the declared uncompressed size and then rejects the CRC.
    That must abort as SanitizeError, and the payload must not land in the output.
    """
    marker = b"CANARYZIPLIE"
    payload = b"Date,Amount\n2025-01-05,-1.00\n" + marker + (b"x" * 8000)
    archive = tmp_path / "lie.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as handle:
        handle.writestr("a.csv", payload)
    _patch_uncompressed_size(archive, 10)
    out = tmp_path / "out"
    with pytest.raises(sz.SanitizeError, match="integrity") as caught:
        sz.sanitize_path(
            archive,
            out,
            limits=sz.Limits(max_file_bytes=100_000, max_total_bytes=1000),
        )
    assert marker.decode() not in str(caught.value)
    leaked = [path for path in out.rglob("*") if path.is_file() and marker in path.read_bytes()]
    assert leaked == []


def test_zip_compression_ratio_uses_bytes_written(tmp_path: Path) -> None:
    payload = b"0" * (2 * 1024 * 1024)
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("a.csv", payload)
    with zipfile.ZipFile(archive) as handle:
        info = handle.getinfo("a.csv")
    assert info.file_size > 1024 * 1024
    assert info.file_size > sz.Limits.max_ratio * max(info.compress_size, 1)
    with pytest.raises(sz.SanitizeError, match="compression ratio"):
        sz.sanitize_path(
            archive,
            tmp_path / "out",
            limits=sz.Limits(max_file_bytes=10_000_000, max_total_bytes=10_000_000),
        )


def test_zip_written_total_stops_a_reader_past_the_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bytes the reader returns still count, even when the header said less."""
    archive = tmp_path / "ok.zip"
    make_zip(archive, [("a.csv", b"Date,Amount\n2025-01-05,-1.00\n", None)])
    real_open = zipfile.ZipFile.open
    extra = bytearray(b"x" * 5000)

    def open_long(
        self: zipfile.ZipFile,
        name: str | zipfile.ZipInfo,
        mode: Literal["r", "w"] = "r",
        pwd: bytes | None = None,
        *,
        force_zip64: bool = False,
    ) -> Any:
        src = real_open(self, name, mode, pwd, force_zip64=force_zip64)

        class Longer:
            def read(self, n: int = -1) -> bytes:
                chunk = src.read(n)
                if chunk:
                    return chunk
                if not extra:
                    return b""
                take = len(extra) if n < 0 else min(n, len(extra))
                piece = bytes(extra[:take])
                del extra[:take]
                return piece

            def __enter__(self) -> Longer:
                return self

            def __exit__(self, *_exc: object) -> bool:
                src.close()
                return False

        return Longer()

    monkeypatch.setattr(zipfile.ZipFile, "open", open_long)
    with pytest.raises(sz.SanitizeError, match="uncompressed"):
        sz.sanitize_path(
            archive,
            tmp_path / "out",
            limits=sz.Limits(max_file_bytes=100_000, max_total_bytes=1000),
        )
