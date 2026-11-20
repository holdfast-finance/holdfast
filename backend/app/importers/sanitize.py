"""Local statement sanitizer: real statements in, safe layout fixtures out.

Runs entirely offline on the owner's machine. Agents never read the input. The output is a
sanitized text/JSON *layout* per document (not a PDF) that keeps structure, column positions,
date formats, label words and sign markers, and replaces everything else (default-deny):

* names, addresses, merchants, any unknown word -> synthetic word of the same length and case
* account / card / routing numbers and every other digit run -> random digits of the same length
* emails -> synthetic ``@example.test`` addresses
* dates -> shifted by one random per-document offset, format kept
* amounts -> multiplied by one secret per-document integer (exact in ``Decimal``), so every linear
  relation survives: opening + sum(txns) == closing, running balances, section totals.

Original values are never logged, printed or written to the report. Output files are 0600 and
the output directory is 0700. See ``docs/ops/statement-sanitizer.md``.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import logging
import os
import random
import re
import secrets
import shutil
import stat
import sys
import tempfile
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".csv", ".ofx", ".qfx"}
RECONCILE_TOLERANCE = Decimal("0.01")


class SanitizeError(Exception):
    """Raised with a fixed reason. Messages never contain file names or document content."""


@dataclass(frozen=True)
class Limits:
    max_files: int = 500
    max_file_bytes: int = 50 * 1024 * 1024
    max_total_bytes: int = 500 * 1024 * 1024
    max_ratio: int = 200  # uncompressed / compressed, for files over 1 MiB


# ---------------------------------------------------------------------------------------------
# Vocabulary. Anything alphabetic that is not here (or passed via --keep) is replaced.
# Words that are also common given names or surnames (will, bill, grant, chase, mark, rose,
# april, june, august...) are deliberately absent.
# ---------------------------------------------------------------------------------------------
_LABEL_WORDS = """
account accounts ach activity additions adjustment adjustments advance advances amount
amounts and annual apr at atm available balance balances basis begin beginning billing bond
bonds bonus brought by card carried cash cashback category change charge charged charges
check checking checks class closing continued contribution contributions core cost cr credit
credits current customer cycle daily date dates days debit debits deposit deposited deposits
description detail details direct dividend dividends dr due earned electronic employee
employer end ending etf fee fees for forward from fund funds gain gross holdings id
important in income information interest last limit loss market mastercard member memo miles
minimum mobile money month mutual name nav net new no note notes notice number of on online
opening other overdraft overview page pages paid past payee payment payments pending
percentage period periods points pos position positions post posted posting previous price
promotional purchase purchases quantity quarter quarterly rate realized redeemed redemption
ref reference refund refunds regular return returned reversal reward rewards routing running
savings security service services settle settlement shares since standard start statement
statements status stock sub subtotal subtractions summary sweep symbol taxes the to total
totals trade trans transaction transactions transfer transfers type unit units unrealized
usd value vested visa week wire with withdrawal withdrawals withdrawn withheld year yearly
ytd
"""
LABELS = frozenset(_LABEL_WORDS.split())

MONTHS: dict[str, int] = {}
for _i in range(1, 13):
    MONTHS[calendar.month_name[_i].lower()] = _i
    MONTHS[calendar.month_abbr[_i].lower()] = _i
MONTHS["sept"] = 9

AMOUNT_RE = re.compile(
    r"(?P<lp>\(?)(?P<neg>[-+]?)(?P<cur>[$£€]?)(?P<neg2>-?)"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+)"
    r"(?P<rp>\)?)(?P<suf>-|CR|DR|Cr|Dr|cr|dr)?(?P<tail>[,;]?)"
)
INT_RE = re.compile(r"(?P<neg>[-+]?)(?P<cur>[$£€]?)(?P<num>\d+)")
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")
EDGE_RE = re.compile(r"(?P<lead>[^\w]*)(?P<core>.*?)(?P<trail>[^\w]*)", re.DOTALL)
PART_RE = re.compile(r"[A-Za-z]+|\d+|[^A-Za-z\d]+")
DAY_RE = re.compile(r"(\d{1,2})([,;]?)")
YEAR_RE = re.compile(r"(\d{4})([,;.]?)")
MONTH_TOKEN_RE = re.compile(r"([A-Za-z]+)(\.?)([,;]?)")
OPEN_RE = re.compile(
    r"(beginning|opening|previous|starting) balance|balance (forward|brought forward)"
)
CLOSE_RE = re.compile(r"(ending|closing|new|statement) balance")

_DATE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ymd", re.compile(r"(\d{4})([-/])(\d{1,2})\2(\d{1,2})")),
    ("mdy", re.compile(r"(\d{1,2})([/-])(\d{1,2})\2(\d{4}|\d{2})")),
    ("dmy", re.compile(r"(\d{1,2})(\.)(\d{1,2})\2(\d{4}|\d{2})")),
    ("md", re.compile(r"(\d{1,2})(/)(\d{1,2})")),
    ("compact", re.compile(r"((?:19|20)\d{2})()(\d{2})(\d{2})")),
]

OFX_KEEP_TAGS = frozenset(
    {"TRNTYPE", "CURDEF", "CODE", "SEVERITY", "ACCTTYPE", "LANGUAGE", "CURRENCY", "INVACCTTYPE"}
)
OFX_AMOUNT_TAGS = frozenset(
    {"TRNAMT", "BALAMT", "TOTAL", "UNITS", "UNITPRICE", "MKTVAL", "AVAILCASH", "MARGINBALANCE"}
)
OFX_ID_TAGS = frozenset(
    {"ACCTID", "BANKID", "ACCTKEY", "BRANCHID", "FITID", "CHECKNUM", "REFNUM", "SRVRTID", "TRNUID"}
)
OFX_HEADER_KEEP = frozenset(
    {"OFXHEADER", "DATA", "VERSION", "SECURITY", "ENCODING", "CHARSET", "COMPRESSION"}
)


# ---------------------------------------------------------------------------------------------
# Layout model
# ---------------------------------------------------------------------------------------------
@dataclass
class Token:
    x0: int
    x1: int
    text: str


@dataclass
class Line:
    y: int
    tokens: list[Token]


@dataclass
class Page:
    width: int
    height: int
    lines: list[Line]


@dataclass
class Doc:
    kind: str  # pdf | csv | ofx
    pages: list[Page]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------------
def _extract_pdf(path: Path) -> Doc:
    import pdfplumber  # imported lazily: only the PDF path needs it

    # pdfminer logs decoded text at DEBUG. Pin it quiet so a debug root logger cannot leak content.
    for noisy in ("pdfminer", "pdfplumber", "PIL"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    pages: list[Page] = []
    warnings: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=2, y_tolerance=3)
            rows: list[tuple[int, list[Token]]] = []
            for w in sorted(words, key=lambda w: (_dpt(w["top"]), _dpt(w["x0"]))):
                tok = Token(_dpt(w["x0"]), _dpt(w["x1"]), str(w["text"]))
                top = _dpt(w["top"])
                if rows and abs(top - rows[-1][0]) <= 30:
                    rows[-1][1].append(tok)
                else:
                    rows.append((top, [tok]))
            lines = [Line(y, sorted(ts, key=lambda t: t.x0)) for y, ts in rows]
            pages.append(Page(_dpt(page.width), _dpt(page.height), lines))
    if not any(p.lines for p in pages):
        warnings.append("no_text_layer_needs_ocr")
    elif any(not p.lines for p in pages):
        warnings.append("some_pages_without_text_layer")
    return Doc("pdf", pages, warnings)


def _dpt(value: object) -> int:
    """Coordinates are stored as integer tenths of a point (layout positions are not money)."""
    return int(Decimal(str(value)) * 10)


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8-sig", errors="replace")


def _extract_csv(path: Path) -> Doc:
    text = _decode(path.read_bytes())
    lines: list[Line] = []
    for n, row in enumerate(csv.reader(io.StringIO(text))):
        lines.append(Line(n, [Token(c, c, cell) for c, cell in enumerate(row)]))
    return Doc("csv", [Page(0, len(lines), lines)])


def _extract_ofx(path: Path) -> Doc:
    text = _decode(path.read_bytes())
    lines = [Line(n, [Token(0, 0, ln)]) for n, ln in enumerate(text.splitlines()) if ln != ""]
    return Doc("ofx", [Page(0, len(lines), lines)])


def extract(path: Path) -> Doc:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".csv":
        return _extract_csv(path)
    return _extract_ofx(path)


# ---------------------------------------------------------------------------------------------
# Sanitizer core
# ---------------------------------------------------------------------------------------------
def _style(word: str, style_from: str) -> str:
    if len(style_from) > 1 and style_from.isupper():
        return word.upper()
    if style_from[:1].isupper():
        return word.capitalize()
    return word


def _is_mask(part: str) -> bool:
    return len(part) >= 2 and set(part.lower()) == {"x"}


class Sanitizer:
    """One instance per document: owns the secret scale, date offset and replacement maps."""

    def __init__(self, seed: int | None = None, keep_words: Iterable[str] = ()) -> None:
        # Not cryptographic: the randomness only has to be unguessable per run, which a
        # SystemRandom-seeded Mersenne generator is. S311 does not apply to fixture noise.
        self._rng = random.Random(seed if seed is not None else secrets.randbits(64))  # noqa: S311
        self.scale = self._rng.randint(3, 9)
        offset = self._rng.randint(30, 540)
        self.shift = timedelta(days=offset if self._rng.randint(0, 1) else -offset)
        self.ref_year = 2024
        self.keep = frozenset(w.lower() for w in keep_words)
        self.replaced: Counter[str] = Counter()
        self.kept: Counter[str] = Counter()
        self._words: dict[str, str] = {}
        self._digits: dict[str, str] = {}

    # -- generators --------------------------------------------------------------------------
    def _synth_word(self, original: str) -> str:
        if original in self._words:
            return self._words[original]
        n = len(original)
        low = original.lower()
        for _ in range(50):
            letters = [
                self._rng.choice("bcdfglmnprstvz" if i % 2 == 0 else "aeiou") for i in range(n)
            ]
            cand = "".join(letters)
            if cand != low and cand not in LABELS and cand not in self.keep:
                break
        out = _style(cand, original)
        self._words[original] = out
        return out

    def _synth_digits(self, original: str) -> str:
        if original in self._digits:
            return self._digits[original]
        out = original
        for _ in range(50):
            out = "".join(self._rng.choice("0123456789") for _ in original)
            if len(original) >= 2 and out[0] == "0":
                continue
            if out != original or len(original) < 3:
                break
        self._digits[original] = out
        return out

    # -- dates -------------------------------------------------------------------------------
    def prescan_years(self, texts: Iterable[str]) -> None:
        years: Counter[int] = Counter()
        for t in texts:
            core = EDGE_RE.fullmatch(t)
            if core and (parsed := self._parse_date(core.group("core"))):
                years[parsed[0].year] += 1
        if years:
            self.ref_year = years.most_common(1)[0][0]

    def _parse_date(self, core: str) -> tuple[date, str, tuple[str, ...]] | None:
        for kind, pat in _DATE_PATTERNS:
            m = pat.fullmatch(core)
            if not m:
                continue
            g = m.groups()
            try:
                if kind in ("ymd", "compact"):
                    y, mo, d = int(g[0]), int(g[2]), int(g[3])
                elif kind == "dmy":
                    d, mo, y = int(g[0]), int(g[2]), self._year(g[3])
                elif kind == "md":
                    y, mo, d = self.ref_year, int(g[0]), int(g[2])
                else:
                    a, mo, y = int(g[0]), int(g[2]), self._year(g[3])
                    if a > 12 >= mo:
                        kind, d, mo = "dmy", a, mo
                    else:
                        d, mo = mo, a
                if kind == "md" and mo == 2 and d == 29:
                    d = 28
                return date(y, mo, d), kind, g
            except ValueError:
                return None
        return None

    @staticmethod
    def _year(s: str) -> int:
        return int(s) if len(s) == 4 else 2000 + int(s)

    def _shift_date(self, core: str) -> str | None:
        parsed = self._parse_date(core)
        if parsed is None:
            return None
        d, kind, g = parsed
        n = d + self.shift
        if kind == "compact":
            return f"{n.year:04d}{n.month:02d}{n.day:02d}"
        sep = g[1]

        def pad(v: int, orig: str) -> str:
            return f"{v:02d}" if len(orig) == 2 else str(v)

        yr = f"{n.year:04d}"
        if kind == "ymd":
            return f"{yr}{sep}{pad(n.month, g[2])}{sep}{pad(n.day, g[3])}"
        if kind == "md":
            return f"{pad(n.month, g[0])}{sep}{pad(n.day, g[2])}"
        year_s = yr if len(g[3]) == 4 else f"{n.year % 100:02d}"
        if kind == "dmy":
            return f"{pad(n.day, g[0])}{sep}{pad(n.month, g[2])}{sep}{year_s}"
        # mdy; the first group was month unless the parser flipped it to dmy
        if g[0] and int(g[0]) > 12:
            return f"{pad(n.day, g[0])}{sep}{pad(n.month, g[2])}{sep}{year_s}"
        return f"{pad(n.month, g[0])}{sep}{pad(n.day, g[2])}{sep}{year_s}"

    def _month_dates(self, texts: list[str], out: list[str], used: list[bool]) -> None:
        n = len(texts)

        def month_of(i: int) -> tuple[int, re.Match[str]] | None:
            if i >= n or used[i]:
                return None
            m = MONTH_TOKEN_RE.fullmatch(texts[i])
            if m and m.group(1).lower() in MONTHS:
                return MONTHS[m.group(1).lower()], m
            return None

        def day_of(i: int) -> re.Match[str] | None:
            if i >= n or used[i]:
                return None
            m = DAY_RE.fullmatch(texts[i])
            return m if m and 1 <= int(m.group(1)) <= 31 else None

        def year_of(i: int) -> re.Match[str] | None:
            if i >= n or used[i]:
                return None
            return YEAR_RE.fullmatch(texts[i])

        def fmt_month(m: re.Match[str], new: date) -> str:
            name = m.group(1)
            full = name.lower() not in {calendar.month_abbr[k].lower() for k in range(1, 13)}
            base = calendar.month_name[new.month] if full else calendar.month_abbr[new.month]
            return _style(base, name) + m.group(2) + m.group(3)

        i = 0
        while i < n:
            if used[i]:
                i += 1
                continue
            mo = month_of(i)
            dm = day_of(i + 1) if mo else None
            if mo and dm:  # Mon DD [YYYY]
                ym = year_of(i + 2)
                self._emit_month_date(
                    texts, out, used, (i, i + 1, i + 2 if ym else None), mo, dm, ym, True
                )
                i += 3 if ym else 2
                continue
            dm = day_of(i)
            mo2 = month_of(i + 1) if dm else None
            if dm and mo2:  # DD Mon [YYYY]
                ym = year_of(i + 2)
                self._emit_month_date(
                    texts, out, used, (i + 1, i, i + 2 if ym else None), mo2, dm, ym, False
                )
                i += 3 if ym else 2
                continue
            i += 1

    def _emit_month_date(
        self,
        texts: list[str],
        out: list[str],
        used: list[bool],
        idx: tuple[int, int, int | None],
        mo: tuple[int, re.Match[str]],
        dm: re.Match[str],
        ym: re.Match[str] | None,
        _month_first: bool,
    ) -> None:
        year = int(ym.group(1)) if ym else self.ref_year
        day = int(dm.group(1))
        try:
            orig = date(year, mo[0], day)
        except ValueError:
            orig = date(year, mo[0], 28)
        new = orig + self.shift
        mi, di, yi = idx
        name = mo[1].group(1)
        full = name.lower() not in {calendar.month_abbr[k].lower() for k in range(1, 13)}
        base = calendar.month_name[new.month] if full else calendar.month_abbr[new.month]
        out[mi] = _style(base, name) + mo[1].group(2) + mo[1].group(3)
        day_s = f"{new.day:02d}" if len(dm.group(1)) == 2 else str(new.day)
        out[di] = day_s + dm.group(2)
        used[mi] = used[di] = True
        if yi is not None and ym is not None:
            out[yi] = f"{new.year:04d}" + ym.group(2)
            used[yi] = True
        self.replaced["date"] += 1
        del texts

    # -- amounts -----------------------------------------------------------------------------
    def _scale(self, num: str) -> str:
        decimals = len(num.split(".")[1]) if "." in num else 0
        val = Decimal(num.replace(",", "")) * self.scale
        return f"{val:,.{decimals}f}" if "," in num else f"{val:.{decimals}f}"

    def scale_amount(self, text: str, *, int_ok: bool = False) -> str | None:
        m = AMOUNT_RE.fullmatch(text)
        if m:
            self.replaced["amount"] += 1
            return (
                m["lp"]
                + m["neg"]
                + m["cur"]
                + m["neg2"]
                + self._scale(m["num"])
                + m["rp"]
                + (m["suf"] or "")
                + m["tail"]
            )
        m2 = INT_RE.fullmatch(text) if int_ok else None
        if m2 and len(m2["num"]) < 8:
            self.replaced["amount"] += 1
            return m2["neg"] + m2["cur"] + self._scale(m2["num"])
        return None

    # -- tokens ------------------------------------------------------------------------------
    def text(self, t: str) -> str:
        """Default-deny sanitize of free text with no amount/date structure."""
        out: list[str] = []
        for part in PART_RE.findall(t):
            if part.isalpha():
                low = part.lower()
                if low in LABELS or low in self.keep:
                    self.kept["label"] += 1
                    out.append(part)
                elif _is_mask(part):
                    self.kept["mask"] += 1
                    out.append(part)
                else:
                    self.replaced["word"] += 1
                    out.append(self._synth_word(part))
            elif part.isdigit():
                if len(part) <= 2:
                    self.kept["small_number"] += 1
                    out.append(part)
                else:
                    self.replaced["account" if len(part) >= 9 else "number"] += 1
                    out.append(self._synth_digits(part))
            else:
                if part.strip():
                    self.kept["punctuation"] += 1
                out.append(part)
        return "".join(out)

    def token(self, t: str, *, int_ok: bool = False) -> str:
        if t == "":
            return t
        if "@" in t:
            m = EDGE_RE.fullmatch(t)
            lead, trail = (m["lead"], m["trail"]) if m else ("", "")
            self.replaced["email"] += 1
            return f"{lead}user{self._rng.randint(100, 999)}@example.test{trail}"
        scaled = self.scale_amount(t, int_ok=int_ok)
        if scaled is not None:
            return scaled
        m = EDGE_RE.fullmatch(t)
        if m and m["core"]:
            core = m["core"]
            if PERCENT_RE.fullmatch(core):
                self.kept["percent"] += 1
                return t
            shifted = self._shift_date(core)
            if shifted is not None:
                self.replaced["date"] += 1
                return m["lead"] + shifted + m["trail"]
        return self.text(t)

    def tokens(self, texts: list[str], *, int_ok: bool = False) -> list[str]:
        out = list(texts)
        used = [False] * len(texts)
        self._month_dates(texts, out, used)
        for i, t in enumerate(texts):
            if not used[i]:
                out[i] = self.token(t, int_ok=int_ok)
        return out

    # -- OFX ---------------------------------------------------------------------------------
    def ofx_line(self, line: str) -> str:
        head = re.fullmatch(r"([A-Z]+):(.*)", line)
        if head:
            if head[1] in OFX_HEADER_KEEP:
                self.kept["ofx_header"] += 1
                return line
            return f"{head[1]}:{self.text(head[2])}"

        def sub(m: re.Match[str]) -> str:
            slash, tag, value = m.group(1), m.group(2), m.group(3)
            if slash or value.strip() == "":
                return m.group(0)
            return f"<{tag}>{self._ofx_value(tag, value)}"

        return re.sub(r"<(/?)([A-Za-z0-9.]+)>([^<\r\n]*)", sub, line)

    def _ofx_value(self, tag: str, value: str) -> str:
        v = value.strip()
        if tag in OFX_KEEP_TAGS:
            self.kept["ofx_enum"] += 1
            return value
        if tag in OFX_AMOUNT_TAGS:
            scaled = self.scale_amount(v, int_ok=True)
            return scaled if scaled is not None else self.text(value)
        if tag.startswith("DT"):
            m = re.fullmatch(r"(\d{8})(.*)", v)
            if (
                m
                and (shifted := self._shift_date(m.group(1)))
                and re.fullmatch(r"[\d.]*(\[[^\]]*\])?", m.group(2))
            ):
                self.replaced["date"] += 1
                return shifted + m.group(2)
            return self.text(value)
        if tag in OFX_ID_TAGS:
            self.replaced["account"] += 1
            return "".join(
                self._rng.choice("0123456789")
                if c.isdigit()
                else _style(self._rng.choice("abcdefghjkmnpqrstuvwxyz"), c)
                if c.isalpha()
                else c
                for c in v
            )
        return self.text(value)


# ---------------------------------------------------------------------------------------------
# Reconciliation check (opening + sum(txns) == closing), run on original and sanitized text
# ---------------------------------------------------------------------------------------------
def _signed(text: str, marker: str = "", *, cr_negative: bool = False) -> Decimal | None:
    """Signed value of an amount token. ``marker`` is a following CR/DR token, if any."""
    m = AMOUNT_RE.fullmatch(text)
    if not m:
        return None
    val = Decimal(m["num"].replace(",", ""))
    if m["lp"] or m["neg"] == "-" or m["neg2"] or m["suf"] == "-":
        val = -val
    tag = (m["suf"] or marker).lower().strip("-")
    if (tag == "cr" and cr_negative) or (tag == "dr" and not cr_negative):
        val = -val
    return val


def _row_amounts(texts: list[str], *, cr_negative: bool) -> list[Decimal]:
    out: list[Decimal] = []
    for i, t in enumerate(texts):
        nxt = texts[i + 1] if i + 1 < len(texts) else ""
        if (a := _signed(t, nxt, cr_negative=cr_negative)) is not None:
            out.append(a)
    return out


def _starts_with_date(texts: list[str]) -> bool:
    if not texts:
        return False
    probe = Sanitizer(seed=0)
    core = EDGE_RE.fullmatch(texts[0])
    if core and probe._parse_date(core["core"]) is not None:
        return True
    return (
        len(texts) > 1
        and MONTH_TOKEN_RE.fullmatch(texts[0]) is not None
        and texts[0].rstrip(".,").lower() in MONTHS
        and DAY_RE.fullmatch(texts[1]) is not None
    )


def check_reconciliation(rows: list[list[str]]) -> bool | None:
    """True/False when opening, closing and transactions were found; None when not detectable."""
    results = [_check(rows, cr_negative=c) for c in (False, True)]
    if True in results:
        return True
    return False if False in results else None


def _check(rows: list[list[str]], *, cr_negative: bool) -> bool | None:
    opening = closing = None
    firsts: list[Decimal] = []
    seconds: list[Decimal] = []
    for texts in rows:
        low = " ".join(texts).lower()
        amts = _row_amounts(texts, cr_negative=cr_negative)
        if OPEN_RE.search(low):
            if opening is None and amts:
                opening = amts[-1]
            continue
        if CLOSE_RE.search(low):
            if closing is None and amts:
                closing = amts[-1]
            continue
        if _starts_with_date(texts) and amts:
            firsts.append(amts[0])
            seconds.append(amts[-1] if len(amts) == 1 else amts[-2])
    if opening is None or closing is None or not firsts:
        return None
    for series in (firsts, seconds):
        total = sum(series, Decimal(0))
        for sign in (1, -1):
            if abs(opening + sign * total - closing) <= RECONCILE_TOLERANCE:
                return True
    return False


# ---------------------------------------------------------------------------------------------
# Document sanitization + rendering
# ---------------------------------------------------------------------------------------------
@dataclass
class SanitizedDoc:
    doc: Doc
    report: dict[str, Any]


def sanitize_doc(
    doc: Doc, *, seed: int | None = None, keep_words: Iterable[str] = ()
) -> SanitizedDoc:
    s = Sanitizer(seed, keep_words)
    s.prescan_years(t.text for p in doc.pages for ln in p.lines for t in ln.tokens)
    before: list[list[str]] = []
    after: list[list[str]] = []
    out_pages: list[Page] = []
    n_tokens = 0
    for page in doc.pages:
        new_lines: list[Line] = []
        for ln in page.lines:
            texts = [t.text for t in ln.tokens]
            if doc.kind == "ofx":
                new = [s.ofx_line(texts[0])]
            elif doc.kind == "csv":
                new = [_csv_cell(s, c) for c in texts]
            else:
                new = s.tokens(texts)
            n_tokens += len(texts)
            before.append(texts)
            after.append(new)
            new_lines.append(
                Line(ln.y, [Token(t.x0, t.x1, n) for t, n in zip(ln.tokens, new, strict=True)])
            )
        out_pages.append(Page(page.width, page.height, new_lines))
    rec_before = check_reconciliation(before) if doc.kind != "ofx" else None
    rec_after = check_reconciliation(after) if doc.kind != "ofx" else None
    survived = rec_before == rec_after
    report: dict[str, Any] = {
        "kind": doc.kind,
        "pages": len(doc.pages),
        "lines": sum(len(p.lines) for p in doc.pages),
        "tokens": n_tokens,
        "replaced": dict(sorted(s.replaced.items())),
        "kept": dict(sorted(s.kept.items())),
        "reconciled_before": rec_before,
        "reconciled_after": rec_after,
        "reconciliation_survived": survived,
        "warnings": list(doc.warnings),
    }
    return SanitizedDoc(Doc(doc.kind, out_pages, doc.warnings), report)


def _csv_cell(s: Sanitizer, cell: str) -> str:
    words = cell.split(" ")
    return " ".join(s.tokens(words, int_ok=len(words) == 1))


def render_text(doc: Doc) -> str:
    """Layout-preserving text. Pages are separated by a form feed."""
    if doc.kind == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        for page in doc.pages:
            for ln in page.lines:
                w.writerow([t.text for t in ln.tokens])
        return buf.getvalue()
    if doc.kind == "ofx":
        return "\n".join(t.text for p in doc.pages for ln in p.lines for t in ln.tokens) + "\n"
    chunks: list[str] = []
    for page in doc.pages:
        cw = max(page.width // 100, 10)  # tenth-points per character cell
        gaps = [b.y - a.y for a, b in zip(page.lines, page.lines[1:], strict=False)]
        typical = sorted(gaps)[len(gaps) // 2] if gaps else 0
        rows: list[str] = []
        prev_y: int | None = None
        for ln in page.lines:
            if prev_y is not None and typical and (ln.y - prev_y) * 10 > 18 * typical:
                rows.append("")
            prev_y = ln.y
            row = ""
            for t in ln.tokens:
                if AMOUNT_RE.fullmatch(t.text):
                    start = max((t.x1 + cw // 2) // cw - len(t.text), 0)
                else:
                    start = t.x0 // cw
                start = max(start, len(row) + (1 if row else 0))
                row = row.ljust(start) + t.text
            rows.append(row)
        chunks.append("\n".join(rows))
    return "\n\f\n".join(chunks) + "\n"


def layout_json(doc: Doc) -> str:
    payload = {
        "version": 1,
        "unit": "0.1pt (csv: column/row index; ofx: line index)",
        "kind": doc.kind,
        "pages": [
            {
                "page": i,
                "width": p.width,
                "height": p.height,
                "lines": [
                    {
                        "y": ln.y,
                        "tokens": [{"x0": t.x0, "x1": t.x1, "text": t.text} for t in ln.tokens],
                    }
                    for ln in p.lines
                ],
            }
            for i, p in enumerate(doc.pages, 1)
        ],
    }
    return json.dumps(payload, indent=1) + "\n"


# ---------------------------------------------------------------------------------------------
# Input gathering: safe zip extraction, symlink rejection, limits
# ---------------------------------------------------------------------------------------------
def _write_private(path: Path, data: bytes | str) -> None:
    raw = data.encode() if isinstance(data, str) else data
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    path.chmod(0o600)


def _safe_member(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in Path(name).parts


def extract_zip(zpath: Path, dest: Path, limits: Limits, start_index: int = 0) -> list[Path]:
    """Extract supported members flat into ``dest`` under synthetic names."""
    out: list[Path] = []
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile as exc:
        raise SanitizeError("input is not a valid zip") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > limits.max_files:
            raise SanitizeError("zip has too many entries")
        if sum(i.file_size for i in infos) > limits.max_total_bytes:
            raise SanitizeError("zip uncompressed size exceeds limit")
        for info in infos:
            if not _safe_member(info.filename):
                raise SanitizeError("zip entry path is unsafe (traversal or absolute)")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise SanitizeError("zip contains a symlink")
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise SanitizeError("zip contains a special file")
            if info.flag_bits & 0x1:
                raise SanitizeError("zip contains an encrypted entry")
        for info in infos:
            if info.is_dir():
                continue
            suffix = Path(info.filename).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                continue
            if info.file_size > limits.max_file_bytes:
                raise SanitizeError("zip entry exceeds the per-file size limit")
            if info.file_size > 1024 * 1024 and info.file_size > limits.max_ratio * max(
                info.compress_size, 1
            ):
                raise SanitizeError("zip entry compression ratio is suspicious")
            target = dest / f"{start_index + len(out):04d}{suffix}"
            written = 0
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh, zf.open(info) as src:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > limits.max_file_bytes:
                        raise SanitizeError("zip entry exceeds the per-file size limit")
                    fh.write(chunk)
            out.append(target)
    return out


def gather_inputs(src: Path, work: Path, limits: Limits) -> list[Path]:
    """Return supported files from a file, directory or zip, as private copies/paths."""
    if src.is_symlink():
        raise SanitizeError("input is a symlink")
    if not src.exists():
        raise SanitizeError("input does not exist")
    found: list[Path] = []
    if src.is_file():
        candidates = [src]
    else:
        candidates = []
        for root, dirs, files in os.walk(src, followlinks=False):
            for name in [*dirs, *files]:
                if (Path(root) / name).is_symlink():
                    raise SanitizeError("input directory contains a symlink")
            dirs.sort()
            candidates.extend(Path(root) / f for f in sorted(files))
    total = 0
    for p in candidates:
        suffix = p.suffix.lower()
        if suffix == ".zip":
            found.extend(extract_zip(p, work, limits, start_index=len(found)))
        elif suffix in SUPPORTED_SUFFIXES:
            size = p.stat().st_size
            total += size
            if size > limits.max_file_bytes or total > limits.max_total_bytes:
                raise SanitizeError("input exceeds the size limits")
            found.append(p)
        if len(found) > limits.max_files:
            raise SanitizeError("too many input files")
    return found


# ---------------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------------
def sanitize_path(
    src: Path,
    out: Path | None,
    *,
    keep_words: Iterable[str] = (),
    seed: int | None = None,
    limits: Limits | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Sanitize ``src`` into ``out`` (or nothing when ``out`` is None). Returns (summary, texts)."""
    limits = limits or Limits()
    if out is not None:
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        out.chmod(0o700)
    work = Path(tempfile.mkdtemp(prefix="holdfast-sanitize-"))
    work.chmod(0o700)
    docs: list[dict[str, Any]] = []
    texts: list[str] = []
    try:
        files = gather_inputs(src, work, limits)
        for n, path in enumerate(files, 1):
            name = f"doc-{n:04d}"
            entry: dict[str, Any] = {"doc": name, "suffix": path.suffix.lower()}
            try:
                entry["sha256_12"] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
                result = sanitize_doc(extract(path), seed=seed, keep_words=keep_words)
            except Exception as exc:
                entry["error"] = type(exc).__name__
                logger.warning("sanitize failed doc=%s error=%s", name, type(exc).__name__)
                docs.append(entry)
                continue
            entry.update(result.report)
            text = render_text(result.doc)
            texts.append(f"===== {name} ({result.doc.kind}) =====\n{text}")
            if out is not None:
                _write_private(out / f"{name}.layout.json", layout_json(result.doc))
                _write_private(out / f"{name}.txt", text)
                _write_private(out / f"{name}.report.json", json.dumps(entry, indent=1) + "\n")
            logger.info(
                "sanitized doc=%s kind=%s replaced=%d",
                name,
                result.doc.kind,
                sum(result.report["replaced"].values()),
            )
            docs.append(entry)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    totals: Counter[str] = Counter()
    for d in docs:
        totals.update(d.get("replaced", {}))
    summary: dict[str, Any] = {
        "documents": len(docs),
        "failed": sum(1 for d in docs if "error" in d),
        "needs_ocr": sum(1 for d in docs if "no_text_layer_needs_ocr" in d.get("warnings", [])),
        "reconciliation_broken": sum(1 for d in docs if d.get("reconciliation_survived") is False),
        "replaced_total": dict(sorted(totals.items())),
        "docs": docs,
    }
    if out is not None:
        _write_private(out / "SUMMARY.json", json.dumps(summary, indent=1) + "\n")
    return summary, texts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.importers.sanitize",
        description="Turn real statements into sanitized layout fixtures, fully offline.",
    )
    ap.add_argument("source", type=Path, help="directory, zip, or single PDF/CSV/OFX/QFX")
    ap.add_argument("out", type=Path, nargs="?", help="output directory (created 0700)")
    ap.add_argument(
        "--review", action="store_true", help="print sanitized output only, write nothing"
    )
    ap.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="WORD",
        help="word to leave as-is (e.g. the institution name)",
    )
    args = ap.parse_args(argv)
    if not args.review and args.out is None:
        ap.error("out is required unless --review is given")
    try:
        summary, texts = sanitize_path(
            args.source, None if args.review else args.out, keep_words=args.keep
        )
    except SanitizeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.review:
        sys.stdout.write("\n".join(texts))
    print(
        f"documents={summary['documents']} failed={summary['failed']} "
        f"needs_ocr={summary['needs_ocr']} "
        f"reconciliation_broken={summary['reconciliation_broken']}",
        file=sys.stderr,
    )
    return 1 if summary["failed"] or summary["reconciliation_broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
