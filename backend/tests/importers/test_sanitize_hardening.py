"""Regression tests for the Bulwark findings on the statement sanitizer (H1-H3, M1, L1-L4).

Synthetic data only: invented names, numbers and merchants.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from app.importers import sanitize as sz

SURNAMES = [
    "SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA", "MILLER", "DAVIS",
    "RODRIGUEZ", "MARTINEZ", "HERNANDEZ", "LOPEZ", "WILSON", "ANDERSON", "THOMAS",
    "TAYLOR", "MOORE", "JACKSON", "MARTIN", "THOMPSON", "HARRIS", "ROBINSON", "WALKER",
    "HENDERSON", "PATTERSON", "FERGUSON", "DICKINSON", "HARRISON", "PETERSON", "MORRISON",
    "HARRINGTON", "BRADFORD", "GALLAGHER", "SHERIDAN", "CRAWFORD", "PEMBERTON", "GUNDERSON",
]  # fmt: skip


def _pdf(path: Path, lines: list[str]) -> None:
    c = canvas.Canvas(str(path))
    c.setFont("Helvetica", 10)
    for i, text in enumerate(lines):
        c.drawString(50, 740 - 16 * i, text)
    c.save()


def _run(src: Path, out: Path, **kw: object) -> dict[str, str]:
    sz.sanitize_path(src, out, **kw)  # type: ignore[arg-type]
    return {p.name: p.read_text() for p in out.iterdir()}


# -- H1 ---------------------------------------------------------------------------------------
def test_h1_layout_width_depends_only_on_sanitized_text(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    _pdf(
        src / "s.pdf",
        [
            "WALTER HENDERSON",
            "Beginning Balance $12,345.67",
            "01/03/2024 PAYROLL ACME $4,812.33 $17,158.00",
            "01/05/2024 STARBUCKS $4.75- $17,153.25",
            "Ending Balance $17,153.25",
        ],
    )
    files = _run(src, tmp_path / "out", seed=1)
    layout = json.loads(files["doc-0001.layout.json"])
    page = layout["pages"][0]
    cw = max(page["width"] // 100, 10)
    for line in page["lines"]:
        for tok in line["tokens"]:
            assert tok["x1"] - tok["x0"] == len(tok["text"]) * cw
            assert tok["x0"] % cw == 0


def test_h1_width_ranking_attack_fails(tmp_path: Path) -> None:
    """Calibrated ranking by glyph width must not single out the true surname."""
    src = tmp_path / "in"
    src.mkdir()
    _pdf(src / "s.pdf", ["WALTER HENDERSON"])
    layout = json.loads(_run(src, tmp_path / "out", seed=2)["doc-0001.layout.json"])
    tok = layout["pages"][0]["lines"][0]["tokens"][1]
    width = (tok["x1"] - tok["x0"]) / 10
    same_len = [n for n in SURNAMES if len(n) == len(tok["text"])]
    ranked = sorted(same_len, key=lambda n: abs(stringWidth(n, "Helvetica", 10) - width))
    # the width is now exactly len * cell: every same-length candidate is off by a coarse margin
    errors = {n: abs(stringWidth(n, "Helvetica", 10) - width) for n in ranked}
    assert not any(err < 0.05 for err in errors.values())


def test_h1_amount_width_hides_digit_count(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    _pdf(
        src / "s.pdf",
        [
            "Beginning Balance $12,345.67",
            "01/03/2024 PAYROLL $4,812.33 $17,158.00",
            "Ending Balance $17,158.00",
        ],
    )
    out = _run(src, tmp_path / "out", seed=3)
    layout = json.loads(out["doc-0001.layout.json"])
    cw = max(layout["pages"][0]["width"] // 100, 10)
    for line in layout["pages"][0]["lines"]:
        for tok in line["tokens"]:
            if "$" in tok["text"]:
                assert tok["x1"] - tok["x0"] == len(tok["text"]) * cw


# -- H2 ---------------------------------------------------------------------------------------
H2_CASES = [
    "Петров,",
    "(Иванов)",
    "Иван Петров ",
    "Γιάννης.",
    "李小龍。",
    "李小龍 ",
    "\uff33\uff4d\uff49\uff54\uff48,",
    "\uff33\uff2d\uff29\uff34\uff28\u3002",
    "Café,",
    "José García;",
    "Café,",
]


@pytest.mark.parametrize("text", H2_CASES)
def test_h2_non_ascii_text_is_replaced(text: str) -> None:
    s = sz.Sanitizer(seed=5)
    for result in (s.text(text), s.token(text)):
        assert all(ord(ch) < 128 for ch in result), result
        assert len(result) == len(text)


def test_h2_ascii_punctuation_and_whitespace_survive() -> None:
    out = sz.Sanitizer(seed=5).text("Иван, (Петров) - x.")
    assert re.fullmatch(r"[A-Za-z]+, \([A-Za-z]+\) - [A-Za-z]\.", out), out


def test_h2_csv_and_ofx_name(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.csv").write_text(
        'Date,Description,Amount\n2024-01-05,"Иван Петров,",-12.50\n2024-01-06,"李小龍。 ",-1.00\n',
        encoding="utf-8",
    )
    (src / "b.ofx").write_text(
        "OFXHEADER:100\n<OFX>\n<STMTTRN><TRNAMT>-12.50<NAME>Петров, Иван.\n"
        "<MEMO>José García;\n</OFX>\n",
        encoding="utf-8",
    )
    files = _run(src, tmp_path / "out", seed=6)
    blob = files["doc-0001.txt"] + files["doc-0002.txt"]
    assert blob.isascii()


# -- H3 ---------------------------------------------------------------------------------------
OFX_BODY = """OFXHEADER:100
DATA:OFXSGML
<OFX>
<STMTTRN><TRNTYPE>DEBIT<TRNAMT>-12.50<FITID>A1<NAME>ACME
<MEMO><![CDATA[WALTER HENDERSON 021000021]]></MEMO>
<!-- acct 123456789 WALTER -->
WALTER HENDERSON 7391 Juniper
</STMTTRN>
</OFX>
"""


def test_h3_ofx_cdata_comments_and_bare_lines(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.ofx").write_text(OFX_BODY)
    text = _run(src, tmp_path / "out", seed=7)["doc-0001.txt"]
    for leaked in ("WALTER", "HENDERSON", "021000021", "123456789", "7391", "Juniper"):
        assert leaked not in text
    assert "<![" in text and "-->" in text  # structure survives


def test_h3_text_before_first_tag_and_unknown_header_key() -> None:
    s = sz.Sanitizer(seed=7)
    assert "HENDERSON" not in s.ofx_line("HENDERSON <OFX>")
    assert "HENDERSON" not in s.ofx_line("HENDERSON:thing")
    assert "HENDERSON" not in s.ofx_line("VERSION:HENDERSON")
    assert s.ofx_line("VERSION:102") == "VERSION:102"


def test_h3_prolog_only_known_keys() -> None:
    s = sz.Sanitizer(seed=7)
    good = '<?OFX OFXHEADER="200" VERSION="211" SECURITY="NONE" OLDFILEUID="NONE"?>'
    assert s.ofx_line(good) == good
    bad = '<?OFX WALTER="HENDERSON" VERSION="211"?>'
    assert "HENDERSON" not in s.ofx_line(bad)
    assert "WALTER" not in s.ofx_line(bad)
    assert "HENDERSON" not in s.ofx_line('<?xml version="1.0" encoding="HENDERSON"?>')


# -- M1 ---------------------------------------------------------------------------------------
def test_m1_budget_is_shared_across_zips_and_plain_files(tmp_path: Path) -> None:
    lim = sz.Limits(max_total_bytes=3000, max_file_bytes=2000)
    src = tmp_path / "in"
    src.mkdir()
    for i in range(5):
        with zipfile.ZipFile(src / f"z{i}.zip", "w") as zf:
            zf.writestr("a.csv", "x" * 1500)
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(sz.SanitizeError):
        sz.gather_inputs(src, work, lim)

    shutil.rmtree(src)
    src.mkdir()
    (src / "a.csv").write_text("y" * 1900)
    with zipfile.ZipFile(src / "b.zip", "w") as zf:
        zf.writestr("c.csv", "x" * 1900)
    with pytest.raises(sz.SanitizeError):
        sz.gather_inputs(src, work, lim)


def test_m1_member_count_is_shared(tmp_path: Path) -> None:
    lim = sz.Limits(max_files=5)
    src = tmp_path / "in"
    src.mkdir()
    for i in range(3):
        with zipfile.ZipFile(src / f"z{i}.zip", "w") as zf:
            zf.writestr("a.csv", "a,b\n")
            zf.writestr("b.csv", "a,b\n")
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(sz.SanitizeError, match="too many"):
        sz.gather_inputs(src, work, lim)
    assert len(list(work.iterdir())) == 4  # third zip refused before extracting


# -- L1 ---------------------------------------------------------------------------------------
def test_l1_grouping_is_document_wide_not_per_token(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    # one grouped original and several ungrouped ones of different magnitudes
    (src / "a.csv").write_text(
        "Date,Description,Amount\n"
        "2024-01-05,A,1234.56\n2024-01-06,B,5.00\n2024-01-07,C,98765.43\n2024-01-08,D,1,234.56\n".replace(
            "1,234.56", '"1,234.56"'
        ),
    )
    text = _run(src, tmp_path / "out", seed=8)["doc-0001.txt"]
    amounts = [row.split(",")[-1].strip('"') for row in text.splitlines()[1:]]
    for amount in amounts:
        mag = float(amount.replace(",", ""))
        assert ("," in amount) == (mag >= 1000), amount


def test_l1_ungrouped_document_stays_ungrouped(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.csv").write_text("Date,Description,Amount\n2024-01-05,A,1234.56\n2024-01-06,B,2.00\n")
    text = _run(src, tmp_path / "out", seed=8)["doc-0001.txt"]
    assert "," not in "".join(row.split(",", 2)[2] for row in text.splitlines()[1:])


# -- L2 ---------------------------------------------------------------------------------------
def test_l2_unsupported_compression_is_a_fixed_error(tmp_path: Path) -> None:
    z = tmp_path / "bz.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("Walter_Henderson_1234.csv", "x")
    raw = bytearray(z.read_bytes())
    raw[8:10] = (9).to_bytes(2, "little")
    cd = raw.find(b"PK\x01\x02")
    raw[cd + 10 : cd + 12] = (9).to_bytes(2, "little")
    z.write_bytes(bytes(raw))
    with pytest.raises(sz.SanitizeError) as err:
        sz.sanitize_path(z, None)
    assert "Henderson" not in str(err.value) and str(tmp_path) not in str(err.value)


def test_l2_main_catch_all_prints_type_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise ValueError("/home/walter/secret-henderson.pdf")

    monkeypatch.setattr(sz, "sanitize_path", boom)
    code = sz.main([str(tmp_path), "--review"])
    err = capsys.readouterr().err
    assert code == 3
    assert "ValueError" in err and "henderson" not in err and "/home" not in err


def test_l2_oserror_maps_to_fixed_message(tmp_path: Path) -> None:
    with pytest.raises(sz.SanitizeError) as err:
        sz.extract_zip(tmp_path / "missing-henderson.zip", tmp_path, sz.Limits())
    assert "henderson" not in str(err.value)


# -- L3 ---------------------------------------------------------------------------------------
def test_l3_ambiguous_words_not_labels_but_keepable() -> None:
    for word in ("mobile", "card", "stock"):
        assert word not in sz.LABELS
        assert sz.Sanitizer(seed=1).text(word.capitalize()) != word.capitalize()
        assert sz.Sanitizer(seed=1, keep_words=[word]).text(word) == word


# -- L4 ---------------------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_l4_symlinked_out_refused(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.csv").write_text("a,b\n")
    target = tmp_path / "elsewhere"
    target.mkdir()
    link = tmp_path / "out"
    link.symlink_to(target)
    with pytest.raises(sz.SanitizeError):
        sz.sanitize_path(src, link)
    assert list(target.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_l4_symlink_inside_out_is_not_followed(tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    with pytest.raises(OSError):
        out = tmp_path / "out"
        out.mkdir()
        (out / "doc-0001.txt").symlink_to(victim)
        sz._write_private(out / "doc-0001.txt", "overwritten")
    assert victim.read_text() == "keep"


def test_l4_non_empty_out_refused_unless_cleared(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.csv").write_text("a,b\n")
    out = tmp_path / "out"
    out.mkdir()
    (out / "stale.txt").write_text("old real data")
    with pytest.raises(sz.SanitizeError, match="not empty"):
        sz.sanitize_path(src, out)
    assert (out / "stale.txt").exists()
    sz.sanitize_path(src, out, clear_out=True)
    assert not (out / "stale.txt").exists()
    assert (out / "doc-0001.txt").exists()
    assert oct(os.stat(out).st_mode & 0o777) == "0o700"


def test_l4_cli_flag(tmp_path: Path) -> None:
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.csv").write_text("a,b\n")
    out = tmp_path / "out"
    assert sz.main([str(src), str(out)]) == 0
    assert sz.main([str(src), str(out)]) == 2
    assert sz.main([str(src), str(out), "--clear-out"]) == 0


def test_regex_part_re_splits_unicode_letters() -> None:
    assert sz.PART_RE.findall("Иван,") == ["Иван", ","]
    assert re.fullmatch(r"[^\W\d_]+", "Café")
