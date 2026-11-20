"""Local statement sanitizer: real statements in, safe layout fixtures out.

Runs entirely offline on the owner's machine. Agents never read the input. The output is a
sanitized text/JSON *layout* per document (not a PDF) that keeps structure, column positions,
date formats, label words and sign markers, and replaces everything else (default-deny):

* names, addresses, merchants, any unknown word -> synthetic word of the same length and case
* account / card / routing numbers and every other digit run -> random digits of the same length
* emails -> synthetic ``@example.test`` addresses
* dates -> a synthetic calendar that keeps format and order, not the original gaps or clock time
* amounts -> independent synthetic figures (exact in ``Decimal``). Opening, running balances,
  section totals and closing are recomputed so opening + sum(txns) == closing. A brokerage row
  keeps quantity x price = value. Nothing is a scale of the original.

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
import string
import sys
import tempfile
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
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
    max_ratio: int = 200  # bytes written / compressed size, for files over 1 MiB
    max_pdf_pages: int = 50


# ---------------------------------------------------------------------------------------------
# Vocabulary. Anything alphabetic that is not here (or passed via --keep) is replaced.
# Words that are also common given names or surnames (will, bill, grant, chase, mark, rose,
# april, june, august...) are deliberately absent.
# ---------------------------------------------------------------------------------------------
_LABEL_WORDS = """
account accounts ach activity additions adjustment adjustments advance advances amount
amounts and annual apr at atm available balance balances basis begin beginning billing
bonds bonus brought by carried cashback category change charge charged charges
check checking checks class closing continued contribution contributions core cost cr credit
credits current customer cycle daily date dates days debit debits deposit deposited deposits
description detail details direct dividend dividends dr due earned electronic employee
employer end ending etf fee fees for forward from fund funds gain holdings id
important in income information interest last limit loss market mastercard member memo
minimum money month mutual name nav net new no note notes notice number of on online
opening other overdraft overview pages paid past payee payment payments pending
percentage period periods points pos position positions posted posting previous
promotional purchase purchases quantity quarter quarterly rate realized redeemed redemption
ref reference refund refunds regular return returned reversal reward rewards routing running
savings security service services settle settlement shares since standard start statement
statements status sub subtotal subtractions summary sweep symbol taxes the to total
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

_NUM = (
    r"\d{1,3}(?:\.\d{3})+(?:,\d+)"  # 1.234,56
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?"  # 1,234.56 or 1,234
    r"|\d+\.\d+"
    r"|\.\d+"
    r"|\d+,\d+"  # 12,50
)
_AMT_BODY = (
    r"(?P<lp>\(?)(?P<neg>[-+]?)(?P<cur>[$£€]?)(?P<neg2>-?)"
    r"(?P<num>" + _NUM + r")"
    r"(?P<rp>\)?)(?P<suf>-|CR|DR|Cr|Dr|cr|dr)?(?P<tail>[,;]?)"
)
AMOUNT_RE = re.compile(_AMT_BODY)
AMOUNT_SEARCH_RE = re.compile(_AMT_BODY)
CUR_INT_RE = re.compile(
    r"(?P<lp>\(?)(?P<neg>[-+]?)(?P<cur>[$£€])(?P<neg2>-?)"
    r"(?P<num>\d+)(?P<rp>\)?)(?P<suf>-|CR|DR|Cr|Dr|cr|dr)?(?P<tail>[,;]?)"
)
INT_RE = re.compile(
    r"(?P<lp>\(?)(?P<neg>[-+]?)(?P<cur>)(?P<neg2>)"
    r"(?P<num>\d+)(?P<rp>\)?)(?P<suf>)(?P<tail>[,;]?)"
)
PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GLUED_DATE_RE = re.compile(r"\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{4}|[A-Za-z]{3,9}[-/]\d{1,2}[-/,]\d{4}")
NUMERIC_DATE_RE = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{1,2}\.\d{1,2}\.\d{2,4}"
)
EDGE_RE = re.compile(r"(?P<lead>[^\w]*)(?P<core>.*?)(?P<trail>[^\w]*)", re.DOTALL)
PART_RE = re.compile(r"[^\W\d_]+|\d+|[\W_]+")
_LETTER_RE = re.compile(r"[^\W\d_]")
_ASCII_PUNCT_WS = frozenset(string.punctuation + " \t\r\n\f\v")
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

OFX_TAG_RE = re.compile(r"<(/?)([A-Za-z0-9.]+)>([^<\r\n]*)")
OFX_PROLOG_RE = re.compile(r"<\?(xml|OFX)((?:\s+[A-Za-z]+=\"[^\"<>]*\")*)\s*\?>")
OFX_ATTR_RE = re.compile(r'([A-Za-z]+)="([^"<>]*)"')
OFX_WRAPPED_RE = re.compile(r"(<!\[CDATA\[|<!--)(.*?)(\]\]>|-->)", re.DOTALL)
# Header and prolog values that may pass verbatim, per key (OFX 1.x/2.x and the XML prolog).
# Everything else goes through the sanitizer.
OFX_HEADER_VALUES: dict[str, frozenset[str]] = {
    "OFXHEADER": frozenset({"100", "200"}),
    "VERSION": frozenset(
        {"102", "103", "151", "160", "200", "201", "202", "203", "210", "211", "220", "221"}
    ),
    "SECURITY": frozenset({"NONE", "TYPE1"}),
    "ENCODING": frozenset({"USASCII", "UTF-8"}),
    "CHARSET": frozenset({"1252", "NONE", "UTF-8", "ISO-8859-1"}),
    "COMPRESSION": frozenset({"NONE"}),
    "DATA": frozenset({"OFXSGML"}),
    "OLDFILEUID": frozenset({"NONE"}),
    "NEWFILEUID": frozenset({"NONE"}),
    "STANDALONE": frozenset({"yes", "no"}),
}
XML_VERSIONS = frozenset({"1.0", "1.1"})
_ISO_4217_TEXT = (
    "AED ARS AUD BGN BRL CAD CHF CLP CNY COP CZK DKK EGP EUR GBP HKD HUF IDR ILS INR ISK JPY "
    "KRW MXN MYR NGN NOK NZD PEN PHP PLN RON RUB SAR SEK SGD THB TRY TWD UAH USD VND ZAR"
)
_TRNTYPE_TEXT = (
    "CREDIT DEBIT INT DIV FEE SRVCHG DEP ATM POS XFER CHECK PAYMENT CASH DIRECTDEP "
    "DIRECTDEBIT REPEATPMT HOLD OTHER"
)
# Enumerated value tags. A kept value must also fully match [A-Z0-9]{1,12}.
OFX_ENUMS: dict[str, frozenset[str]] = {
    "TRNTYPE": frozenset(_TRNTYPE_TEXT.split()),
    "ACCTTYPE": frozenset({"CHECKING", "SAVINGS", "MONEYMRKT", "CREDITLINE", "CD"}),
    "INVACCTTYPE": frozenset({"INDIVIDUAL", "JOINT", "TRUST", "CORPORATE"}),
    "SEVERITY": frozenset({"INFO", "WARN", "ERROR"}),
    "CURDEF": frozenset(_ISO_4217_TEXT.split()),
}
OFX_KEEP_VALUE_RE = re.compile(r"[A-Z0-9]{1,12}")
# OFX 1.x/2.x element names. Anything else is renamed (a tag name can carry text).
_OFX_TAG_TEXT = """OFX SIGNONMSGSRSV1 SONRS STATUS CODE SEVERITY MESSAGE DTSERVER LANGUAGE DTPROFUP
    DTACCTUP FI ORG FID INTU.BID BANKMSGSRSV1 STMTTRNRS TRNUID CLTCOOKIE STMTRS CURDEF
    BANKACCTFROM BANKID BRANCHID ACCTID ACCTTYPE ACCTKEY BANKTRANLIST DTSTART DTEND STMTTRN
    TRNTYPE DTPOSTED DTUSER DTAVAIL TRNAMT FITID CORRECTFITID CORRECTACTION SRVRTID CHECKNUM
    REFNUM SIC PAYEEID NAME PAYEE EXTDNAME ADDR1 ADDR2 ADDR3 CITY STATE POSTALCODE COUNTRY
    PHONE BANKACCTTO CCACCTTO MEMO INV401KSOURCE CURRENCY ORIGCURRENCY CURRATE CURSYM
    LEDGERBAL BALAMT DTASOF AVAILBAL BALLIST BAL DESC BALTYPE VALUE DTTM CCSTMTTRNRS CCSTMTRS
    CCACCTFROM CREDITCARDMSGSRSV1 INVSTMTMSGSRSV1 INVSTMTTRNRS INVSTMTRS DTASOF INVACCTFROM
    BROKERID INVTRANLIST INVBANKTRAN SUBACCTFUND BUYSTOCK BUYMF BUYOPT BUYDEBT BUYOTHER
    SELLSTOCK SELLMF SELLOPT SELLDEBT SELLOTHER INCOME REINVEST INVBUY INVSELL INVTRAN
    SECID UNIQUEID UNIQUEIDTYPE UNITS UNITPRICE MKTVAL MARKUP COMMISSION TAXES FEES LOAD
    TOTAL SUBACCTSEC SUBACCTFUND ACCRDINT INCOMETYPE INVPOSLIST POSSTOCK POSMF POSOPT
    POSDEBT POSOTHER INVPOS HELDINSTRUMENT POSTYPE UNITSSTREET UNITSUSER DTPRICEASOF
    MEMO INVBAL AVAILCASH MARGINBALANCE SHORTBALANCE BUYPOWER BALLIST SECLISTMSGSRSV1
    SECLIST STOCKINFO MFINFO OPTINFO DEBTINFO OTHERINFO SECINFO SECNAME TICKER FIID RATING
    ASSETCLASS FIASSETCLASS PARVALUE DEBTTYPE DEBTCLASS COUPONRT DTCOUPON COUPONFREQ
    CALLPRICE YIELDTOCALL DTCALL CALLTYPE YIELDTOMAT DTMAT MFTYPE YIELD DTYIELDASOF
    OPTTYPE STRIKEPRICE DTEXPIRE SHPERCTRCT SECID2 ASSETCLASSPORTION OPENBAL CLOSEBAL
    TAXEXEMPT WITHHOLDING SELLREASON OPTBUYTYPE OPTSELLTYPE RELFITID LOANID GAIN LOSS
       INVEXPENSE JRNLSEC JRNLFUND RETOFCAP SPLIT CLOSUREOPT
    BANKMSGSRQV1 STMTTRNRQ STMTRQ INCTRAN INCLUDE INCPOS INCBAL SONRQ USERID USERPASS
    DTCLIENT APPID APPVER GENUSERKEY TRNRS TRNRQ SIGNONMSGSRQV1 CCSTMTTRNRQ CCSTMTRQ
    INVSTMTTRNRQ INVSTMTRQ  MKTGINFO  SUBACCTTO SUBACCTFROM """
OFX_TAGS = frozenset(_OFX_TAG_TEXT.split())
INTU_TAG_RE = re.compile(r"INTU\.[A-Z0-9]{1,16}")
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
OFX_TXN_TAGS = frozenset({"TRNAMT"})
OFX_BAL_TAGS = frozenset({"BALAMT", "AVAILCASH", "MARGINBALANCE"})
_BROKER_QTY = frozenset({"quantity", "qty", "units", "shares"})
_BROKER_PRICE = frozenset({"price", "unitprice", "unit price"})
_BROKER_VALUE = frozenset({"value", "mktval", "marketvalue", "market value"})


def _safe_header_value(key: str, value: str, *, xml: bool = False) -> bool:
    """True when ``value`` is a spec value for header/prolog ``key``."""
    if xml and key.upper() == "VERSION":
        return value in XML_VERSIONS
    allowed = OFX_HEADER_VALUES.get(key if key == "standalone" else key.upper())
    if key.lower() == "standalone":
        allowed = OFX_HEADER_VALUES["STANDALONE"]
    return allowed is not None and value in allowed


def _keep_value_ok(tag: str, value: str) -> bool:
    if OFX_KEEP_VALUE_RE.fullmatch(value) is None:
        return False
    enum = OFX_ENUMS.get(tag)
    return enum is None or value in enum


def _amount_match(text: str, *, int_ok: bool = False) -> re.Match[str] | None:
    found = AMOUNT_RE.fullmatch(text) or CUR_INT_RE.fullmatch(text)
    if found is None and int_ok:
        found = INT_RE.fullmatch(text)
        if found is not None and len(found.group("num") or "") >= 8:
            return None
    return found


def _num_is_euro(num: str) -> bool:
    if "," in num and "." in num:
        return num.rfind(",") > num.rfind(".")
    if "," in num and "." not in num:
        parts = num.split(",")
        return not all(len(part) == 3 for part in parts[1:])
    return False


def _num_decimals(num: str, *, euro: bool) -> int:
    if euro:
        return len(num.split(",")[-1]) if "," in num else 0
    if num.startswith("."):
        return len(num) - 1
    if "." in num:
        return len(num.split(".")[-1])
    return 0


def _parse_num(num: str, *, euro: bool) -> Decimal:
    raw = num.replace(".", "").replace(",", ".") if euro else num.replace(",", "")
    if raw.startswith("."):
        raw = "0" + raw
    return Decimal(raw)


def _format_mag(mag: Decimal, decimals: int, *, grouped: bool, euro: bool, lead_dot: bool) -> str:
    quantum = abs(mag)
    text = f"{quantum:,.{decimals}f}" if grouped or euro else f"{quantum:.{decimals}f}"
    if euro:
        text = text.replace(",", "X").replace(".", ",").replace("X", ".")
    if lead_dot and text.startswith("0."):
        text = text[1:]
    return text


@dataclass
class _Amt:
    line: int
    tok: int
    raw: str
    match: re.Match[str]
    signed: Decimal
    role: str


def _render_signed(match: re.Match[str], signed: Decimal, *, grouped: bool) -> str:
    """Format ``signed`` in the currency style of ``match``.

    Thousands grouping is a document-wide choice applied to the synthetic value. It is never
    read from the original token, which would leak a magnitude bucket.
    """
    num = match.group("num") or "0"
    euro = _num_is_euro(num)
    decimals = _num_decimals(num, euro=euro)
    lead_dot = num.startswith(".")
    body = _format_mag(abs(signed), decimals, grouped=grouped, euro=euro, lead_dot=lead_dot)
    cur = match.group("cur") or ""
    tail = match.group("tail") or ""
    if signed < 0 and match.group("lp"):
        return f"({cur}{body}){tail}"
    if signed < 0:
        return f"-{cur}{body}{tail}"
    return f"{cur}{body}{tail}"


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
def _extract_pdf(path: Path, *, max_pages: int) -> Doc:
    try:
        import pdfplumber  # imported lazily: only the PDF path needs it
    except ImportError as exc:
        raise SanitizeError("pdf support is not installed") from exc

    # pdfminer logs decoded text at DEBUG. Pin it quiet so a debug root logger cannot leak content.
    for noisy in ("pdfminer", "pdfplumber", "PIL"):
        logging.getLogger(noisy).setLevel(logging.ERROR)

    pages: list[Page] = []
    warnings: list[str] = []
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) > max_pages:
            raise SanitizeError("pdf has too many pages")
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


def extract(path: Path, *, limits: Limits | None = None) -> Doc:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        cap = limits.max_pdf_pages if limits is not None else Limits.max_pdf_pages
        return _extract_pdf(path, max_pages=cap)
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
    """One instance per document: owns the synthetic calendar and replacement maps."""

    def __init__(self, seed: int | None = None, keep_words: Iterable[str] = ()) -> None:
        # Not cryptographic: the randomness only has to be unguessable per run, which a
        # SystemRandom-seeded Mersenne generator is. S311 does not apply to fixture noise.
        self._rng = random.Random(seed if seed is not None else secrets.randbits(64))  # noqa: S311
        self.ref_year = 2024
        self.keep = frozenset(w.lower() for w in keep_words)
        self.replaced: Counter[str] = Counter()
        self.kept: Counter[str] = Counter()
        self._words: dict[str, str] = {}
        self._digits: dict[str, str] = {}
        self._seen_dates: list[date] = []
        self._date_map: dict[date, date] = {}
        self._dates_frozen = False
        self._cell_plan: dict[tuple[int, int], str] = {}
        self._ofx_plan: dict[tuple[int, int], str] = {}
        self._ofx_used: Counter[int] = Counter()
        self._tag_map: dict[str, str] = {}
        self.grouped = False  # document-wide thousands grouping, set by observe_grouping

    # -- generators --------------------------------------------------------------------------
    def _synth_word(self, original: str) -> str:
        if original in self._words:
            return self._words[original]
        n = len(original)
        low = original.lower()
        cand = low
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
            if out != original:
                break
        if out == original and original:
            chars = list(original)
            chars[-1] = "0" if chars[-1] != "0" else "1"
            out = "".join(chars)
        self._digits[original] = out
        return out

    def observe_grouping(self, rows: list[list[str]]) -> None:
        """One document-wide bit: does this document group thousands with separators?"""
        for texts in rows:
            for text in texts:
                for found in AMOUNT_SEARCH_RE.finditer(text):
                    num = found.group("num") or ""
                    if "," in num and not _num_is_euro(num):
                        self.grouped = True
                        return

    # -- dates -------------------------------------------------------------------------------
    def prescan_years(self, texts: Iterable[str]) -> None:
        years: Counter[int] = Counter()
        for t in texts:
            core = EDGE_RE.fullmatch(t)
            if core and (parsed := self._parse_date(core.group("core"))):
                years[parsed[0].year] += 1
        if years:
            self.ref_year = years.most_common(1)[0][0]

    def observe_lines(self, rows: list[list[str]], *, kind: str) -> None:
        """Learn every date before assigning the synthetic calendar."""
        for texts in rows:
            self._note_row_dates(texts, kind=kind, year_only=True)
        if self._seen_dates:
            years: Counter[int] = Counter(item.year for item in self._seen_dates)
            self.ref_year = years.most_common(1)[0][0]
        for texts in rows:
            self._note_row_dates(texts, kind=kind, year_only=False)
        self._freeze_dates()

    def _note_row_dates(self, texts: list[str], *, kind: str, year_only: bool) -> None:
        if kind == "ofx":
            for raw in texts:
                for found in re.finditer(r"<D[A-Z0-9.]*>(\d{8})", raw):
                    parsed = self._parse_date(found.group(1))
                    if parsed is not None:
                        self._note_date(parsed[0])
                if not year_only:
                    self._note_glued(raw)
            return
        for raw in texts:
            core = EDGE_RE.fullmatch(raw)
            if core is not None:
                parsed = self._parse_date(core.group("core"))
                has_year = re.search(r"\d{4}", core.group("core"))
                if parsed is not None and (not year_only or has_year):
                    self._note_date(parsed[0])
            if not year_only:
                self._note_glued(raw)
        if not year_only:
            self._note_month_line(texts)

    def _note_date(self, value: date) -> None:
        if value not in self._seen_dates:
            self._seen_dates.append(value)

    def _note_glued(self, raw: str) -> None:
        for found in GLUED_DATE_RE.finditer(raw):
            parsed = self._glued_date(found.group(0))
            if parsed is not None:
                self._note_date(parsed)

    def _glued_date(self, text: str) -> date | None:
        dmy = re.fullmatch(r"(\d{1,2})[-/]([A-Za-z]{3,9})[-/](\d{4})", text)
        mdy = re.fullmatch(r"([A-Za-z]{3,9})[-/](\d{1,2})[-/,](\d{4})", text)
        if dmy is not None:
            month = MONTHS.get(dmy.group(2).lower())
            day, year = int(dmy.group(1)), int(dmy.group(3))
        elif mdy is not None:
            month = MONTHS.get(mdy.group(1).lower())
            day, year = int(mdy.group(2)), int(mdy.group(3))
        else:
            return None
        if month is None:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 28)

    def _note_month_line(self, texts: list[str]) -> None:
        count = len(texts)
        index = 0
        while index < count:
            month = MONTH_TOKEN_RE.fullmatch(texts[index])
            day = DAY_RE.fullmatch(texts[index + 1]) if index + 1 < count else None
            if (
                month is not None
                and month.group(1).lower() in MONTHS
                and day is not None
                and 1 <= int(day.group(1)) <= 31
            ):
                year_m = YEAR_RE.fullmatch(texts[index + 2]) if index + 2 < count else None
                self._note_parts(int(day.group(1)), MONTHS[month.group(1).lower()], year_m)
                index += 3 if year_m is not None else 2
                continue
            day = DAY_RE.fullmatch(texts[index])
            month = MONTH_TOKEN_RE.fullmatch(texts[index + 1]) if index + 1 < count else None
            if (
                day is not None
                and 1 <= int(day.group(1)) <= 31
                and month is not None
                and month.group(1).lower() in MONTHS
            ):
                year_m = YEAR_RE.fullmatch(texts[index + 2]) if index + 2 < count else None
                self._note_parts(int(day.group(1)), MONTHS[month.group(1).lower()], year_m)
                index += 3 if year_m is not None else 2
                continue
            index += 1

    def _note_parts(self, day: int, month: int, year_m: re.Match[str] | None) -> None:
        year = int(year_m.group(1)) if year_m is not None else self.ref_year
        try:
            self._note_date(date(year, month, day))
        except ValueError:
            self._note_date(date(year, month, 28))

    def _freeze_dates(self) -> None:
        if self._dates_frozen:
            return
        self._dates_frozen = True
        ordered = sorted(set(self._seen_dates))
        if not ordered:
            return
        anchor = date(2016, 6, 15)
        for _ in range(20):
            candidate = date(
                self._rng.randint(2016, 2023),
                self._rng.randint(1, 12),
                self._rng.randint(1, 28),
            )
            if candidate != ordered[0] and candidate.day != ordered[0].day:
                anchor = candidate
                break
        previous_original: date | None = None
        previous_new: date | None = None
        for original in ordered:
            if previous_original is None or previous_new is None:
                assigned = anchor
                gap = 0
            else:
                gap = (original - previous_original).days
                if gap <= 0:
                    assigned = previous_new
                else:
                    choices = [step for step in range(1, max(gap * 3, 21)) if step != gap]
                    assigned = previous_new + timedelta(days=self._rng.choice(choices))
            if previous_new is not None and gap > 0:
                assigned = self._separate(assigned, original, previous_new, gap)
            elif assigned == original or assigned.day == original.day:
                assigned = assigned + timedelta(days=3)
            self._date_map[original] = assigned
            previous_original = original
            previous_new = assigned

    def _separate(self, assigned: date, original: date, previous: date, gap: int) -> date:
        for _ in range(6):
            moved = (assigned - previous).days
            if assigned != original and assigned.day != original.day and moved != gap and moved > 0:
                return assigned
            assigned = assigned + timedelta(days=1)
        return assigned

    def _map_date(self, original: date) -> date:
        if not self._dates_frozen:
            self._note_date(original)
            self._freeze_dates()
        mapped = self._date_map.get(original)
        if mapped is not None:
            return mapped
        return self._insert_date(original)

    def _insert_date(self, original: date) -> date:
        keys = sorted(self._date_map)
        if not keys:
            assigned = date(2016, 6, 15)
        else:
            earlier = [key for key in keys if key < original]
            later = [key for key in keys if key > original]
            if earlier:
                gap = (original - earlier[-1]).days
                base = self._date_map[earlier[-1]]
                choices = (
                    [1]
                    if gap <= 0
                    else [step for step in range(1, max(gap * 3, 21)) if step != gap]
                )
                assigned = base + timedelta(days=self._rng.choice(choices))
                if later and assigned >= self._date_map[later[0]]:
                    assigned = base + timedelta(days=1)
            else:
                gap = (later[0] - original).days
                limit = self._date_map[later[0]]
                choices = (
                    [1]
                    if gap <= 0
                    else [step for step in range(1, max(gap * 3, 21)) if step != gap]
                )
                assigned = limit - timedelta(days=self._rng.choice(choices))
        if assigned == original or assigned.day == original.day:
            assigned = assigned + timedelta(days=3)
        self._date_map[original] = assigned
        return assigned

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
        n = self._map_date(d)
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
        new = self._map_date(orig)
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
    def _draw_mag(self, decimals: int, *, forbid: Decimal | None = None) -> Decimal:
        """A positive amount on the original's grid. Not a function of that amount."""
        span = (10 ** (decimals + 4)) - 1 if decimals else 99_999
        span = max(span, 2)
        forbid_units: int | None = None
        if forbid is not None:
            forbid_units = int((abs(forbid) * (Decimal(10) ** decimals)).to_integral_value())
        for _ in range(40):
            units = self._rng.randint(1, span)
            if forbid_units is not None and units == forbid_units:
                continue
            if decimals:
                return Decimal(units) / (Decimal(10) ** decimals)
            return Decimal(units)
        return Decimal(1)

    def _fresh_amount(self, text: str, *, int_ok: bool = False) -> str | None:
        match = _amount_match(text, int_ok=int_ok)
        if match is None:
            return None
        num = match.group("num") or "0"
        euro = _num_is_euro(num)
        decimals = _num_decimals(num, euro=euro)
        original = _parse_num(num, euro=euro)
        magnitude = self._draw_mag(decimals, forbid=original)
        body = _format_mag(
            magnitude, decimals, grouped=self.grouped, euro=euro, lead_dot=num.startswith(".")
        )
        self.replaced["amount"] += 1
        return text[: match.start("num")] + body + text[match.end("num") :]

    def scale_amount(self, text: str, *, int_ok: bool = False) -> str | None:
        """Backward-compatible name. Draws a fresh amount; it does not scale."""
        return self._fresh_amount(text, int_ok=int_ok)

    # -- tokens ------------------------------------------------------------------------------
    def _deny(self, text: str) -> str:
        """Replace every word and every digit run. No length is exempt."""
        out: list[str] = []
        for part in PART_RE.findall(text):
            if _LETTER_RE.match(part):
                low = part.lower()
                if part.isascii() and (low in LABELS or low in self.keep):
                    self.kept["label"] += 1
                    out.append(part)
                elif _is_mask(part):
                    self.kept["mask"] += 1
                    out.append(part)
                else:
                    self.replaced["word"] += 1
                    out.append(self._synth_word(part))
            elif part[:1].isdecimal():
                self.replaced["account" if len(part) >= 9 else "number"] += 1
                out.append(self._synth_digits(part))
            else:
                out.append(self._scrub_punct(part))
        return "".join(out)

    def _scrub_punct(self, run: str) -> str:
        """Keep ASCII punctuation and whitespace. Anything else is replaced one for one.

        Non-ASCII symbols, combining marks, zero-width and exotic spaces can carry name or
        script information, so they never pass through verbatim.
        """
        if all(ch in _ASCII_PUNCT_WS for ch in run):
            if run.strip():
                self.kept["punctuation"] += 1
            return run
        out: list[str] = []
        for ch in run:
            if ch in _ASCII_PUNCT_WS:
                out.append(ch)
            else:
                self.replaced["symbol"] += 1
                out.append(self._rng.choice("bcdfglmnprstvz"))
        return "".join(out)

    def _spans(self, text: str) -> list[tuple[int, int, str, str]]:
        found: list[tuple[int, int, int, str, str]] = []
        rules = (
            (0, EMAIL_RE, "email"),
            (1, GLUED_DATE_RE, "date"),
            (1, NUMERIC_DATE_RE, "date"),
            (2, PERCENT_RE, "percent"),
            (3, AMOUNT_SEARCH_RE, "amount"),
            (3, CUR_INT_RE, "amount"),
        )
        for priority, pattern, kind in rules:
            for match in pattern.finditer(text):
                found.append(
                    (match.start(), -(match.end() - match.start()), priority, kind, match.group(0))
                )
        found.sort()
        chosen: list[tuple[int, int, str, str]] = []
        cursor = 0
        for start, neg_len, _priority, kind, piece in found:
            end = start - neg_len
            if start < cursor:
                continue
            chosen.append((start, end, kind, piece))
            cursor = end
        return chosen

    def _rewrite_span(self, kind: str, piece: str) -> str:
        if kind == "email":
            self.replaced["email"] += 1
            return f"user{self._rng.randint(100, 999)}@example.test"
        if kind == "percent":
            return self._fresh_percent(piece)
        if kind == "date":
            glued = self._format_glued(piece)
            if glued is not None:
                self.replaced["date"] += 1
                return glued
            shifted = self._shift_date(piece)
            if shifted is not None:
                self.replaced["date"] += 1
                return shifted
            return self._deny(piece)
        fresh = self._fresh_amount(piece)
        return fresh if fresh is not None else self._deny(piece)

    def _fresh_percent(self, text: str) -> str:
        body = text[:-1] if text.endswith("%") else text
        decimals = len(body.split(".")[1]) if "." in body else 0
        try:
            forbid: Decimal | None = Decimal(body)
        except InvalidOperation:
            forbid = None
        magnitude = self._draw_mag(decimals, forbid=forbid)
        self.replaced["percent"] += 1
        if decimals:
            return f"{magnitude:.{decimals}f}%"
        return f"{int(magnitude)}%"

    def _format_glued(self, text: str) -> str | None:
        dmy = re.fullmatch(r"(\d{1,2})([-/])([A-Za-z]{3,9})([-/])(\d{4})", text)
        mdy = re.fullmatch(r"([A-Za-z]{3,9})([-/])(\d{1,2})([-,/])(\d{4})", text)
        parsed = self._glued_date(text)
        if parsed is None:
            return None
        mapped = self._map_date(parsed)
        if dmy is not None:
            month_text = dmy.group(3)
            sep_a, sep_b = dmy.group(2), dmy.group(4)
            day_src = dmy.group(1)
            month_first = False
        elif mdy is not None:
            month_text = mdy.group(1)
            sep_a, sep_b = mdy.group(2), mdy.group(4)
            day_src = mdy.group(3)
            month_first = True
        else:
            return None
        full = month_text.lower() not in {calendar.month_abbr[k].lower() for k in range(1, 13)}
        base = calendar.month_name[mapped.month] if full else calendar.month_abbr[mapped.month]
        month = _style(base, month_text)
        day = f"{mapped.day:02d}" if len(day_src) == 2 else str(mapped.day)
        if month_first:
            return f"{month}{sep_a}{day}{sep_b}{mapped.year:04d}"
        return f"{day}{sep_a}{month}{sep_b}{mapped.year:04d}"

    def text(self, t: str) -> str:
        """Default-deny sanitize of free text. Amounts, dates and emails are rewritten first."""
        if t == "":
            return t
        if not self._dates_frozen:
            self.observe_lines([[t]], kind="pdf")
        spans = self._spans(t)
        if not spans:
            return self._deny(t)
        parts: list[str] = []
        pos = 0
        for start, end, kind, piece in spans:
            if start > pos:
                parts.append(self._deny(t[pos:start]))
            parts.append(self._rewrite_span(kind, piece))
            pos = end
        if pos < len(t):
            parts.append(self._deny(t[pos:]))
        return "".join(parts)

    def _keep_page_index(self, prev: str | None, text: str) -> bool:
        if prev is None or not re.fullmatch(r"\d{1,4}", text):
            return False
        if prev.lower().rstrip(".:") != "page":
            return False
        return "page" in LABELS or "page" in self.keep

    def token(self, t: str, *, int_ok: bool = False, prev: str | None = None) -> str:
        if t == "":
            return t
        if not self._dates_frozen:
            self.observe_lines([[t]], kind="pdf")
        if self._keep_page_index(prev, t):
            self.kept["page"] += 1
            return t
        if "@" in t:
            edge = EDGE_RE.fullmatch(t)
            core = edge.group("core") if edge is not None else t
            if EMAIL_RE.fullmatch(core):
                lead = self._scrub_punct(edge.group("lead")) if edge is not None else ""
                trail = self._scrub_punct(edge.group("trail")) if edge is not None else ""
                self.replaced["email"] += 1
                return f"{lead}user{self._rng.randint(100, 999)}@example.test{trail}"
        glued = self._format_glued(t)
        if glued is not None:
            self.replaced["date"] += 1
            return glued
        edge = EDGE_RE.fullmatch(t)
        if edge is not None and edge.group("core"):
            core = edge.group("core")
            shifted = self._shift_date(core)
            if shifted is not None:
                self.replaced["date"] += 1
                return (
                    self._scrub_punct(edge.group("lead"))
                    + shifted
                    + self._scrub_punct(edge.group("trail"))
                )
            if PERCENT_RE.fullmatch(core):
                return (
                    self._scrub_punct(edge.group("lead"))
                    + self._fresh_percent(core)
                    + self._scrub_punct(edge.group("trail"))
                )
        fresh = self._fresh_amount(t, int_ok=int_ok)
        if fresh is not None:
            return fresh
        return self.text(t)

    def tokens(
        self, texts: list[str], *, int_ok: bool = False, line_no: int | None = None
    ) -> list[str]:
        if not self._dates_frozen:
            self.observe_lines([texts], kind="pdf")
        out = list(texts)
        used = [False] * len(texts)
        self._month_dates(texts, out, used)
        previous: str | None = None
        for index, original in enumerate(texts):
            if used[index]:
                previous = original
                continue
            planned = self._cell_plan.get((line_no, index)) if line_no is not None else None
            if planned is not None:
                out[index] = planned
                self.replaced["amount"] += 1
            else:
                out[index] = self.token(original, int_ok=int_ok, prev=previous)
            previous = original
        return out

    def csv_cell(self, cell: str, line_no: int, tok: int) -> str:
        planned = self._cell_plan.get((line_no, tok))
        if planned is not None:
            self.replaced["amount"] += 1
            return planned
        words = cell.split(" ")
        return " ".join(self.tokens(words, int_ok=len(words) == 1))

    # -- reconciliation plan -----------------------------------------------------------------
    def plan(self, rows: list[list[str]], *, kind: str) -> None:
        if kind == "ofx":
            self._plan_ofx(rows)
        else:
            self._plan_table(rows, kind=kind)

    def _plan_table(self, rows: list[list[str]], *, kind: str) -> None:
        mode = False
        if _check(rows, cr_negative=False) is not True and _check(rows, cr_negative=True) is True:
            mode = True
        header = _broker_header(rows[0]) if kind == "csv" and rows else {}
        broker = {"qty", "price", "value"} <= set(header.values())
        sites: list[_Amt] = []
        seen_open = False
        seen_close = False
        int_ok = kind == "csv"
        for line_no, texts in enumerate(rows):
            if broker and line_no == 0:
                continue
            amounts = _amount_indexes(texts, int_ok=int_ok)
            if broker and line_no > 0:
                for column, role in header.items():
                    if column >= len(texts):
                        continue
                    match = _amount_match(texts[column], int_ok=True)
                    if match is None:
                        continue
                    signed = _signed(texts[column], cr_negative=mode) or Decimal(0)
                    sites.append(_Amt(line_no, column, texts[column], match, signed, role))
                    if column in amounts:
                        amounts.remove(column)
            if not amounts:
                continue
            low = " ".join(texts).lower()
            dated = _starts_with_date(texts)
            roles = _roles_for_line(
                texts,
                amounts,
                low=low,
                dated=dated,
                seen_open=seen_open,
                seen_close=seen_close,
                mode=mode,
            )
            if "opening" in roles.values():
                seen_open = True
            if "closing" in roles.values():
                seen_close = True
            for column in amounts:
                match = _amount_match(texts[column], int_ok=int_ok and " " not in texts[column])
                if match is None:
                    continue
                nxt = texts[column + 1] if column + 1 < len(texts) else ""
                signed = _signed(texts[column], nxt, cr_negative=mode) or Decimal(0)
                sites.append(_Amt(line_no, column, texts[column], match, signed, roles[column]))
        self._assign_table(rows, sites)

    def _assign_table(self, rows: list[list[str]], sites: list[_Amt]) -> None:
        txns = [site for site in sites if site.role == "txn"]
        opening = next((site for site in sites if site.role == "opening"), None)
        closing = next((site for site in sites if site.role == "closing"), None)
        txn_values = [self._signed_draw(site) for site in txns]
        if _is_uniform_scale([site.signed for site in txns], txn_values):
            txn_values = [self._signed_draw(site) for site in txns]
        txn_sum = sum(txn_values, Decimal(0))
        close_signed: Decimal | None = None
        if closing is not None:
            close_signed = self._signed_draw(closing)
        if opening is not None and close_signed is not None:
            open_signed = close_signed - txn_sum
        elif opening is not None:
            open_signed = self._signed_draw(opening)
        elif close_signed is not None:
            open_signed = close_signed - txn_sum
        else:
            open_signed = self._draw_mag(2)
        balance = open_signed
        section = Decimal(0)
        txn_at = 0
        by_line: dict[int, list[_Amt]] = {}
        for site in sites:
            by_line.setdefault(site.line, []).append(site)
        for line_no in range(len(rows)):
            row_sites = by_line.get(line_no, [])
            line_txns: list[tuple[_Amt, Decimal]] = []
            for site in row_sites:
                if site.role == "txn":
                    line_txns.append((site, txn_values[txn_at]))
                    txn_at += 1
            for _site, signed in line_txns:
                balance += signed
                section += signed
            balance_after = balance
            qty = next((site for site in row_sites if site.role == "qty"), None)
            price = next((site for site in row_sites if site.role == "price"), None)
            qty_mag: Decimal | None = None
            price_mag: Decimal | None = None
            product: Decimal | None = None
            if qty is not None and price is not None:
                qty_mag = self._draw_mag(_match_decimals(qty.match), forbid=abs(qty.signed))
                price_mag = self._draw_mag(_match_decimals(price.match), forbid=abs(price.signed))
                product = qty_mag * price_mag
            for site in row_sites:
                if site.role == "txn":
                    signed = next(value for item, value in line_txns if item is site)
                    display = _inject(site, abs(signed), grouped=self.grouped)
                elif site.role == "opening":
                    display = _render_signed(site.match, open_signed, grouped=self.grouped)
                elif site.role == "closing" and close_signed is not None:
                    display = _render_signed(site.match, close_signed, grouped=self.grouped)
                elif site.role == "running":
                    display = _render_signed(site.match, balance_after, grouped=self.grouped)
                elif site.role == "section":
                    display = _render_signed(site.match, section, grouped=self.grouped)
                    section = Decimal(0)
                elif site.role == "qty" and qty_mag is not None:
                    display = _inject(site, qty_mag, grouped=self.grouped)
                elif site.role == "price" and price_mag is not None:
                    display = _inject(site, price_mag, grouped=self.grouped)
                elif site.role == "value" and product is not None:
                    display = _render_product(site.match, product, grouped=self.grouped)
                else:
                    display = _inject(
                        site,
                        self._draw_mag(_match_decimals(site.match), forbid=abs(site.signed)),
                        grouped=self.grouped,
                    )
                self._cell_plan[(site.line, site.tok)] = display

    def _signed_draw(self, site: _Amt) -> Decimal:
        sign = -1 if site.signed < 0 else 1
        return sign * self._draw_mag(_match_decimals(site.match), forbid=abs(site.signed))

    def _plan_ofx(self, rows: list[list[str]]) -> None:
        events: list[tuple[int, int, str, Decimal, re.Match[str]]] = []
        for line_no, texts in enumerate(rows):
            raw = texts[0] if texts else ""
            nth = 0
            for found in re.finditer(r"<([A-Za-z0-9.]+)>([^<\r\n]*)", raw):
                tag, value = found.group(1), found.group(2).strip()
                if tag not in OFX_AMOUNT_TAGS:
                    continue
                match = _amount_match(value, int_ok=True)
                if match is not None:
                    events.append((line_no, nth, tag, _signed(value) or Decimal(0), match))
                nth += 1
        txns = [item for item in events if item[2] in OFX_TXN_TAGS]
        bals = [item for item in events if item[2] in OFX_BAL_TAGS]
        originals = [item[3] for item in txns]
        drawn = [self._signed_ofx(item[3], item[4]) for item in txns]
        if _is_uniform_scale(originals, drawn):
            drawn = [self._signed_ofx(item[3], item[4]) for item in txns]
        txn_sum = sum(drawn, Decimal(0))
        original_sum = sum(originals, Decimal(0))
        identity = (
            len(bals) >= 2 and abs(bals[0][3] + original_sum - bals[-1][3]) <= RECONCILE_TOLERANCE
        )
        open_signed = Decimal(0)
        close_signed: Decimal | None = None
        if identity:
            close_signed = self._signed_ofx(bals[-1][3], bals[-1][4])
            open_signed = close_signed - txn_sum
        balance = open_signed
        txn_at = 0
        seen_txn_before_bal = 0
        for line_no, nth, tag, signed, match in events:
            if tag in OFX_TXN_TAGS:
                value = drawn[txn_at]
                txn_at += 1
                balance += value
                seen_txn_before_bal += 1
                display = _render_signed(match, value, grouped=self.grouped)
            elif identity and bals and (line_no, nth) == (bals[0][0], bals[0][1]):
                display = _render_signed(match, open_signed, grouped=self.grouped)
            elif identity and bals and (line_no, nth) == (bals[-1][0], bals[-1][1]):
                display = _render_signed(match, close_signed or Decimal(0), grouped=self.grouped)
            elif tag in {"UNITS", "UNITPRICE", "MKTVAL"}:
                display = _render_signed(
                    match, self._signed_ofx(signed, match), grouped=self.grouped
                )
                self._ofx_plan[(line_no, nth)] = display
                continue
            else:
                display = _render_signed(
                    match, self._signed_ofx(signed, match), grouped=self.grouped
                )
            self._ofx_plan[(line_no, nth)] = display
        self._tie_ofx_broker(rows)

    def _signed_ofx(self, signed: Decimal, match: re.Match[str]) -> Decimal:
        sign = -1 if signed < 0 else 1
        return sign * self._draw_mag(_match_decimals(match), forbid=abs(signed))

    def _tie_ofx_broker(self, rows: list[list[str]]) -> None:
        """When UNITS, UNITPRICE and MKTVAL sit on one transaction, value is the product."""
        pending: dict[str, tuple[int, int, re.Match[str]]] = {}
        for line_no, texts in enumerate(rows):
            raw = texts[0] if texts else ""
            if raw.startswith(("<STMTTRN>", "<INVBUY>", "<INVSELL>")):
                pending = {}
            nth = 0
            for found in re.finditer(r"<([A-Za-z0-9.]+)>([^<\r\n]*)", raw):
                tag, value = found.group(1), found.group(2).strip()
                if tag not in OFX_AMOUNT_TAGS:
                    continue
                match = _amount_match(value, int_ok=True)
                if match is not None and tag in {"UNITS", "UNITPRICE", "MKTVAL"}:
                    pending[tag] = (line_no, nth, match)
                nth += 1
            if {"UNITS", "UNITPRICE", "MKTVAL"} <= pending.keys():
                units = pending["UNITS"]
                price = pending["UNITPRICE"]
                market = pending["MKTVAL"]
                quantity = self._draw_mag(_match_decimals(units[2]))
                unit_price = self._draw_mag(_match_decimals(price[2]))
                product = quantity * unit_price
                self._ofx_plan[(units[0], units[1])] = _render_signed(
                    units[2], quantity, grouped=self.grouped
                )
                self._ofx_plan[(price[0], price[1])] = _render_signed(
                    price[2], unit_price, grouped=self.grouped
                )
                self._ofx_plan[(market[0], market[1])] = _render_signed(
                    market[2], product, grouped=self.grouped
                )
                pending = {}

    # -- OFX ---------------------------------------------------------------------------------
    def ofx_line(self, line: str, line_no: int = 0) -> str:
        head = re.fullmatch(r"([A-Z]+):(.*)", line)
        if head and head[1] in OFX_HEADER_KEEP and _safe_header_value(head[1], head[2]):
            self.kept["ofx_header"] += 1
            return line
        if head and "<" not in line:
            key = head[1] if head[1] in OFX_HEADER_KEEP else self.text(head[1])
            return f"{key}:{self.token(head[2])}"
        cursor = 0

        def sub(match: re.Match[str]) -> str:
            nonlocal cursor
            slash, tag, value = match.group(1), match.group(2), match.group(3)
            shown = self._ofx_tag(tag)
            if slash:
                return f"</{shown}>" + self._ofx_gap(value)
            if value.strip() == "":
                return f"<{shown}>{value}"
            if tag in OFX_AMOUNT_TAGS:
                key = (line_no, cursor)
                cursor += 1
                planned = self._ofx_plan.get(key)
                if planned is not None:
                    self.replaced["amount"] += 1
                    return f"<{shown}>{planned}"
            return f"<{shown}>{self._ofx_value(tag, value)}"

        out: list[str] = []
        pos = 0
        for found in OFX_TAG_RE.finditer(line):
            if found.start() > pos:
                out.append(self._ofx_gap(line[pos : found.start()]))
            out.append(sub(found))
            pos = found.end()
        if pos < len(line):
            out.append(self._ofx_gap(line[pos:]))
        return "".join(out)

    def _ofx_tag(self, tag: str) -> str:
        """Known OFX element names pass. Anything else gets a stable synthetic name."""
        if tag in OFX_TAGS or INTU_TAG_RE.fullmatch(tag):
            return tag
        if tag not in self._tag_map:
            self._tag_map[tag] = f"X.TAG{len(self._tag_map) + 1}"
        return self._tag_map[tag]

    @property
    def renamed_tags(self) -> int:
        return len(self._tag_map)

    def _ofx_gap(self, gap: str) -> str:
        """Sanitize text the tag regex did not claim: prologs, CDATA, comments, bare lines."""
        if all(ch in " \t\r\n" for ch in gap):
            return gap
        lead = gap[: len(gap) - len(gap.lstrip(" \t\r\n"))]
        trail = gap[len(gap.rstrip(" \t\r\n")) :]
        core = gap.strip(" \t\r\n")
        prolog = OFX_PROLOG_RE.fullmatch(core)
        if prolog is not None:
            attrs: list[str] = []
            for key, value in OFX_ATTR_RE.findall(prolog.group(2)):
                if key.upper() not in OFX_HEADER_VALUES:
                    return lead + self.text(core) + trail
                is_xml = prolog.group(1) == "xml"
                shown = value if _safe_header_value(key, value, xml=is_xml) else self._deny(value)
                attrs.append(f' {key}="{shown}"')
            self.kept["ofx_header"] += 1
            return f"{lead}<?{prolog.group(1)}{''.join(attrs)}?>{trail}"
        wrapped = OFX_WRAPPED_RE.fullmatch(core)
        if wrapped is not None:
            return f"{lead}{wrapped.group(1)}{self.text(wrapped.group(2))}{wrapped.group(3)}{trail}"
        return lead + self.text(core) + trail

    def _ofx_value(self, tag: str, value: str) -> str:
        stripped = value.strip()
        if tag in OFX_KEEP_TAGS and _keep_value_ok(tag, stripped):
            self.kept["ofx_enum"] += 1
            return value
        if tag in OFX_KEEP_TAGS:
            return self.token(value)
        if tag in OFX_AMOUNT_TAGS:
            fresh = self._fresh_amount(stripped, int_ok=True)
            return fresh if fresh is not None else self.token(value)
        if tag.startswith("DT"):
            found = re.fullmatch(r"(\d{8})(.*)", stripped)
            if (
                found
                and (shifted := self._shift_date(found.group(1)))
                and re.fullmatch(r"[\d.]*(\[[^\]]*\])?", found.group(2))
            ):
                self.replaced["date"] += 1
                return shifted + self._deny(found.group(2))
            return self.token(value)
        if tag in OFX_ID_TAGS:
            self.replaced["account"] += 1
            return "".join(
                self._rng.choice("0123456789")
                if char.isdigit()
                else _style(self._rng.choice("abcdefghjkmnpqrstuvwxyz"), char)
                if char.isalpha()
                else char
                for char in stripped
            )
        return self.token(value)


# ---------------------------------------------------------------------------------------------
# Reconciliation check (opening + sum(txns) == closing), run on original and sanitized text
# ---------------------------------------------------------------------------------------------
def _match_decimals(match: re.Match[str]) -> int:
    num = match.group("num") or "0"
    return _num_decimals(num, euro=_num_is_euro(num))


def _inject(site: _Amt, magnitude: Decimal, *, grouped: bool) -> str:
    num = site.match.group("num") or "0"
    euro = _num_is_euro(num)
    body = _format_mag(
        magnitude,
        _num_decimals(num, euro=euro),
        grouped=grouped,
        euro=euro,
        lead_dot=num.startswith("."),
    )
    return site.raw[: site.match.start("num")] + body + site.raw[site.match.end("num") :]


def _render_product(match: re.Match[str], product: Decimal, *, grouped: bool) -> str:
    decimals = _match_decimals(match)
    quantum = Decimal(10) ** -decimals if decimals else Decimal(1)
    if product.quantize(quantum, rounding=ROUND_HALF_EVEN) == product:
        return _render_signed(match, product, grouped=grouped)
    cur = match.group("cur") or ""
    sign = "-" if product < 0 else ""
    return f"{sign}{cur}{format(product, 'f')}"


def _broker_header(cells: list[str]) -> dict[int, str]:
    roles: dict[int, str] = {}
    for index, cell in enumerate(cells):
        word = cell.strip().lower()
        if word in _BROKER_QTY:
            roles[index] = "qty"
        elif word in _BROKER_PRICE:
            roles[index] = "price"
        elif word in _BROKER_VALUE:
            roles[index] = "value"
    return roles


def _amount_indexes(texts: list[str], *, int_ok: bool) -> list[int]:
    found: list[int] = []
    for index, raw in enumerate(texts):
        if " " in raw:
            continue
        if _amount_match(raw, int_ok=int_ok) is not None:
            found.append(index)
    return found


def _looks_like_product(texts: list[str], amounts: list[int], mode: bool) -> bool:
    values: list[Decimal] = []
    for index in amounts[-3:]:
        nxt = texts[index + 1] if index + 1 < len(texts) else ""
        values.append(_signed(texts[index], nxt, cr_negative=mode) or Decimal(0))
    qty, price, value = values
    if qty == 0 or price == 0:
        return False
    return abs(abs(qty) * abs(price) - abs(value)) <= Decimal("0.05")


def _roles_for_line(
    texts: list[str],
    amounts: list[int],
    *,
    low: str,
    dated: bool,
    seen_open: bool,
    seen_close: bool,
    mode: bool,
) -> dict[int, str]:
    other = {index: "other" for index in amounts}
    if not dated and not seen_open and OPEN_RE.search(low):
        other[amounts[-1]] = "opening"
        return other
    if not dated and not seen_close and CLOSE_RE.search(low):
        other[amounts[-1]] = "closing"
        return other
    if not dated and re.search(r"\b(?:subtotal|total)\b", low):
        other[amounts[-1]] = "section"
        return other
    if dated and len(amounts) >= 3 and _looks_like_product(texts, amounts, mode):
        other[amounts[-3]] = "qty"
        other[amounts[-2]] = "price"
        other[amounts[-1]] = "value"
        return other
    if dated and len(amounts) >= 2:
        other[amounts[-2]] = "txn"
        other[amounts[-1]] = "running"
        return other
    if dated:
        other[amounts[0]] = "txn"
    return other


def _is_uniform_scale(old: list[Decimal], new: list[Decimal]) -> bool:
    if len(old) < 2 or len(old) != len(new):
        return False
    ratios: list[Decimal] = []
    for before, after in zip(old, new, strict=True):
        if before == 0:
            return False
        ratios.append(after / before)
    first = ratios[0]
    if any(ratio != first for ratio in ratios):
        return False
    if first != first.to_integral_value():
        return False
    return Decimal(2) <= first <= Decimal(30)


def _signed(text: str, marker: str = "", *, cr_negative: bool = False) -> Decimal | None:
    """Signed value of an amount token. ``marker`` is a following CR/DR token, if any."""
    match = _amount_match(text)
    if match is None:
        return None
    num = match.group("num") or ""
    val = _parse_num(num, euro=_num_is_euro(num))
    negative = (
        match.group("lp")
        or match.group("neg") == "-"
        or match.group("neg2")
        or match.group("suf") == "-"
    )
    if negative:
        val = -val
    tag = (match.group("suf") or marker).lower().strip("-")
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
    rows = [[tok.text for tok in ln.tokens] for page in doc.pages for ln in page.lines]
    s.observe_lines(rows, kind=doc.kind)
    s.observe_grouping(rows)
    s.plan(rows, kind=doc.kind)
    before: list[list[str]] = []
    after: list[list[str]] = []
    out_pages: list[Page] = []
    n_tokens = 0
    flat = 0
    for page in doc.pages:
        new_lines: list[Line] = []
        for ln in page.lines:
            texts = rows[flat]
            if doc.kind == "ofx":
                new = [s.ofx_line(texts[0], flat)] if texts else [""]
            elif doc.kind == "csv":
                new = [s.csv_cell(cell, flat, index) for index, cell in enumerate(texts)]
            else:
                new = s.tokens(texts, line_no=flat)
            flat += 1
            n_tokens += len(texts)
            before.append(texts)
            after.append(new)
            new_lines.append(Line(ln.y, _place_tokens(doc.kind, page, ln, new)))
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
        "renamed_tags": s.renamed_tags,
    }
    return SanitizedDoc(Doc(doc.kind, out_pages, doc.warnings), report)


def cell_width(page: Page) -> int:
    """Tenth-points per character cell. Shared by the renderer and the layout snapping."""
    return max(page.width // 100, 10)


def _place_tokens(kind: str, page: Page, line: Line, texts: list[str]) -> list[Token]:
    """Tokens for the sanitized line, with geometry that carries no source glyph widths.

    PDF tokens snap to the character-cell grid and take their width from the *sanitized* text
    length. Amounts anchor on the snapped right edge (they are right-aligned in the source),
    everything else on the snapped left edge. CSV and OFX positions are indexes, kept as-is.
    """
    if kind != "pdf":
        return [Token(t.x0, t.x1, n) for t, n in zip(line.tokens, texts, strict=True)]
    cw = cell_width(page)
    placed: list[Token] = []
    for tok, new in zip(line.tokens, texts, strict=True):
        span = len(new) * cw
        if AMOUNT_RE.fullmatch(new):
            x1 = ((tok.x1 + cw // 2) // cw) * cw
            placed.append(Token(max(x1 - span, 0), max(x1 - span, 0) + span, new))
        else:
            x0 = (tok.x0 // cw) * cw
            placed.append(Token(x0, x0 + span, new))
    return placed


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
        cw = cell_width(page)
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
    """Create ``path`` 0600. Never follows a symlink and never overwrites an existing file."""
    raw = data.encode() if isinstance(data, str) else data
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
        os.fchmod(fh.fileno(), 0o600)


@dataclass
class _Budget:
    """One file and byte budget shared by every zip and plain file in a run."""

    files: int = 0
    bytes: int = 0


# Raised by the zip, zlib and file layers on hostile or damaged input. The messages can carry
# paths, so they are never propagated: callers map them to a fixed SanitizeError.
_READ_ERRORS = (NotImplementedError, EOFError, zlib.error, OSError, RuntimeError)


def _safe_member(name: str) -> bool:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", name):
        return False
    return ".." not in Path(name).parts


def extract_zip(
    zpath: Path,
    dest: Path,
    limits: Limits,
    start_index: int = 0,
    budget: _Budget | None = None,
) -> list[Path]:
    """Extract supported members flat into ``dest`` under synthetic names.

    ``budget`` is shared across the whole run so several zips cannot each stay under a
    per-zip limit while exceeding it together.
    """
    budget = budget if budget is not None else _Budget()
    out: list[Path] = []
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile as exc:
        raise SanitizeError("input is not a valid zip") from exc
    except _READ_ERRORS:
        raise SanitizeError("input could not be read") from None
    with zf:
        infos = zf.infolist()
        budget.files += len(infos)
        if len(infos) > limits.max_files or budget.files > limits.max_files:
            raise SanitizeError("zip has too many entries")
        if sum(i.file_size for i in infos) > limits.max_total_bytes:
            raise SanitizeError("zip uncompressed size exceeds limit")
        declared = sum(
            i.file_size
            for i in infos
            if not i.is_dir() and Path(i.filename).suffix.lower() in SUPPORTED_SUFFIXES
        )
        if budget.bytes + declared > limits.max_total_bytes:
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
            target = dest / f"{start_index + len(out):04d}{suffix}"
            written = 0
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh, zf.open(info) as src:
                    while chunk := src.read(1024 * 1024):
                        written += len(chunk)
                        budget.bytes += len(chunk)
                        if written > limits.max_file_bytes:
                            raise SanitizeError("zip entry exceeds the per-file size limit")
                        if budget.bytes > limits.max_total_bytes:
                            raise SanitizeError("zip uncompressed size exceeds limit")
                        fh.write(chunk)
                if written > info.file_size:
                    raise SanitizeError("zip entry is larger than its declared size")
                if written > 1024 * 1024 and written > limits.max_ratio * max(
                    info.compress_size, 1
                ):
                    raise SanitizeError("zip entry compression ratio is suspicious")
            except zipfile.BadZipFile as exc:
                target.unlink(missing_ok=True)
                raise SanitizeError("zip entry failed integrity check") from exc
            except SanitizeError:
                target.unlink(missing_ok=True)
                raise
            except _READ_ERRORS:
                target.unlink(missing_ok=True)
                raise SanitizeError("zip entry could not be read") from None
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
    budget = _Budget()
    for p in candidates:
        suffix = p.suffix.lower()
        if suffix == ".zip":
            found.extend(extract_zip(p, work, limits, start_index=len(found), budget=budget))
        elif suffix in SUPPORTED_SUFFIXES:
            try:
                size = p.stat().st_size
            except OSError:
                raise SanitizeError("input could not be read") from None
            budget.bytes += size
            budget.files += 1
            if size > limits.max_file_bytes or budget.bytes > limits.max_total_bytes:
                raise SanitizeError("input exceeds the size limits")
            if budget.files > limits.max_files:
                raise SanitizeError("too many input files")
            found.append(p)
        if len(found) > limits.max_files:
            raise SanitizeError("too many input files")
    return found


# ---------------------------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------------------------
_OUR_OUTPUT_RE = re.compile(r"doc-\d{4}\.(?:txt|layout\.json|report\.json)|SUMMARY\.json")


def _prepare_out(out: Path, *, clear: bool, src: Path | None = None) -> None:
    """Create or validate the output directory. Refuses symlinks, overlap and stale content.

    ``--clear-out`` only ever deletes this tool's own output names. Every entry is validated
    before the first unlink, so a refusal leaves the directory untouched.
    """
    if out.is_symlink():
        raise SanitizeError("output directory is a symlink")
    try:
        if src is not None:
            real_src, real_out = src.resolve(), out.resolve()
            if real_src == real_out or real_out in real_src.parents or real_src in real_out.parents:
                raise SanitizeError("output and input paths overlap")
        if out.exists():
            if not out.is_dir():
                raise SanitizeError("output path is not a directory")
            stale = list(out.iterdir())
            if stale and not clear:
                raise SanitizeError("output directory is not empty (pass --clear-out to wipe it)")
            for entry in stale:
                if _OUR_OUTPUT_RE.fullmatch(entry.name) is None or not (
                    entry.is_symlink() or entry.is_file()
                ):
                    raise SanitizeError("output directory holds files this tool did not write")
            for entry in stale:
                entry.unlink()
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        out.chmod(0o700)
    except OSError:
        raise SanitizeError("output directory could not be prepared") from None


def sanitize_path(
    src: Path,
    out: Path | None,
    *,
    keep_words: Iterable[str] = (),
    seed: int | None = None,
    limits: Limits | None = None,
    clear_out: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Sanitize ``src`` into ``out`` (or nothing when ``out`` is None). Returns (summary, texts)."""
    limits = limits or Limits()
    if out is not None:
        _prepare_out(out, clear=clear_out, src=src)
    work = Path(tempfile.mkdtemp(prefix="holdfast-sanitize-"))
    work.chmod(0o700)
    docs: list[dict[str, Any]] = []
    texts: list[str] = []
    try:
        try:
            files = gather_inputs(src, work, limits)
        except OSError:
            raise SanitizeError("input could not be read") from None
        for n, path in enumerate(files, 1):
            name = f"doc-{n:04d}"
            entry: dict[str, Any] = {"doc": name, "suffix": path.suffix.lower()}
            try:
                result = sanitize_doc(
                    extract(path, limits=limits), seed=seed, keep_words=keep_words
                )
            except SanitizeError:
                raise
            except Exception as exc:
                entry["error"] = type(exc).__name__
                logger.warning("sanitize failed doc=%s error=%s", name, type(exc).__name__)
                docs.append(entry)
                continue
            entry.update(result.report)
            text = render_text(result.doc)
            entry["sha256_12"] = hashlib.sha256(text.encode()).hexdigest()[:12]
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
        "--clear-out",
        action="store_true",
        help="empty an existing OUT first (a non-empty OUT is refused without this)",
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
            args.source,
            None if args.review else args.out,
            keep_words=args.keep,
            clear_out=args.clear_out,
        )
    except SanitizeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # last resort: report the type only, never a message or traceback
        print(f"error: unexpected {type(exc).__name__}", file=sys.stderr)
        return 3
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
