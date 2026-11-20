"""Statement sanitizer tests. Everything here is synthetic: invented people, numbers, merchants."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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


def test_dates_shift_by_one_consistent_offset() -> None:
    s = sz.Sanitizer(seed=3)
    a, b = s.token("01/05/2025"), s.token("01/15/2025")
    da, db = (datetime.strptime(x, "%m/%d/%Y") for x in (a, b))
    assert (db - da).days == 10
    assert a != "01/05/2025"
    assert s.token("2025-01-05")[4] == "-"
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
    assert len(blob.split("<DTPOSTED>")[1].split("\n")[0]) == 14


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
