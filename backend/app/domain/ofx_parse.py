"""Parse bank and credit-card statements from OFX 1.x SGML or OFX 2.x XML/QFX."""

import codecs
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException
from xml.etree import ElementTree as ET

from app.domain.statement_parse import StatementParseError
from app.domain.tabular_statement import ExportRows, TransactionExport

_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_OFX_ROOT = re.compile(r"<OFX\s*>", re.IGNORECASE)
_SGML_LEAF = re.compile(r"<([A-Z][A-Z0-9_.-]*)>([^<]*)", re.IGNORECASE)
_BARE_AMP = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")
_DATE = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})(?:[0-9]{6}(?:\.[0-9]+)?)?(?:\[[^\[\]]+\])?")
_AMOUNT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")


@dataclass(frozen=True, kw_only=True)
class OfxStatement(TransactionExport):
    account_last4: str
    currency: str
    period_start: date | None
    period_end: date | None


def _decode(source: str | bytes) -> str:
    if isinstance(source, str):
        return source.removeprefix("\ufeff")
    encoding = "utf-8-sig"
    if source.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif source.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        header = source.split(b"<", 1)[0]
        charset = re.search(rb"(?im)^CHARSET:[ \t]*([\w-]+)", header)
        if charset:
            name = charset[1].decode("ascii").lower()
            encoding = {"1252": "cp1252", "8859-1": "iso-8859-1", "usascii": "ascii"}.get(
                name, name
            )
            if name == "none":
                encoding = "utf-8-sig"
        else:
            declaration = re.match(
                rb"""\s*<\?xml\s+[^>]*?encoding\s*=\s*['"]([\w.-]+)['"]""",
                source,
            )
            if declaration and not source.startswith(codecs.BOM_UTF8):
                encoding = declaration[1].decode("ascii")
    try:
        return source.decode(encoding).removeprefix("\ufeff")
    except (UnicodeError, LookupError):
        raise StatementParseError("OFX cannot be decoded") from None


def _tree(text: str) -> ET.Element:
    if "\x00" in text or _DECLARATION.search(text):
        raise StatementParseError("OFX disallows DOCTYPE and ENTITY declarations and NUL bytes")
    text = text.lstrip()
    root = _OFX_ROOT.search(text)
    prefix = text[: root.start()] if root else text
    sgml = prefix.upper().startswith(("OFXHEADER:", "DATA:"))
    if sgml and root is None:
        raise StatementParseError("OFX root not found")

    if not sgml:
        try:
            return ET.fromstring(text)  # noqa: S314 - unsafe declarations rejected above
        except ET.ParseError:
            if root is None or root.start() != 0:
                raise StatementParseError("malformed OFX XML") from None

    # OFX 1.x omits scalar end tags. Aggregate tags still require an explicit end tag.
    body = text[root.start() :] if root else text

    def close_leaf(match: re.Match[str]) -> str:
        tag, value = match.groups()
        if not value.strip() and tag.upper() not in {"NAME", "MEMO", "FITID", "TRNAMT", "DTPOSTED"}:
            return match[0]
        value = _BARE_AMP.sub("&amp;", value)
        if body[match.end() : match.end() + len(tag) + 3].upper() == f"</{tag.upper()}>":
            return f"<{tag}>{value}"
        return f"<{tag}>{value}</{tag}>"

    try:
        return ET.fromstring(_SGML_LEAF.sub(close_leaf, body))  # noqa: S314 - checked above
    except ET.ParseError as exc:
        raise StatementParseError(f"malformed OFX near line {exc.position[0]}") from None


def _value(element: ET.Element, tag: str, *, required: bool = False) -> str:
    fields = element.findall(tag)
    if len(fields) > 1 or (fields and len(fields[0])):
        raise StatementParseError(f"invalid OFX {tag}")
    value = (fields[0].text or "").strip() if fields else ""
    if not value and required:
        raise StatementParseError(f"missing OFX {tag}")
    return value


def _date(value: str) -> date:
    match = _DATE.fullmatch(value)
    if match:
        try:
            return date(*(int(part) for part in match.groups()))
        except ValueError:
            pass
    raise StatementParseError("unreadable OFX date")


def _amount(value: str) -> Decimal:
    if not _AMOUNT.fullmatch(value):
        raise StatementParseError("unreadable OFX amount")
    return Decimal(value)


def _balance(element: ET.Element) -> Decimal | None:
    balance = _value(element, "BALAMT")
    return _amount(balance) if balance else None


def _statement(element: ET.Element) -> OfxStatement:
    account_tag = "BANKACCTFROM" if element.tag == "STMTRS" else "CCACCTFROM"
    account = _value(element, f"{account_tag}/ACCTID", required=True)
    currency = _value(element, "CURDEF", required=True).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise StatementParseError("invalid OFX currency")

    transaction_list = element.find("BANKTRANLIST")
    ledger = element.find("LEDGERBAL")
    closing = None if ledger is None else _amount(_value(ledger, "BALAMT", required=True))
    start = end = ""
    rows = ExportRows()
    if transaction_list is not None:
        start = _value(transaction_list, "DTSTART")
        end = _value(transaction_list, "DTEND")
        seen: set[str] = set()
        for row_number, txn in enumerate(transaction_list.findall("STMTTRN"), start=1):
            try:
                posted = _date(_value(txn, "DTPOSTED", required=True))
                amount = _amount(_value(txn, "TRNAMT", required=True))
                fitid = _value(txn, "FITID", required=True)
                if fitid in seen:
                    raise StatementParseError("duplicate FITID")
                description = _value(txn, "NAME") or _value(txn, "MEMO")
                rows.add(posted, description, amount, _balance(txn), fitid)
                seen.add(fitid)
            except StatementParseError as exc:
                raise StatementParseError(f"OFX transaction row {row_number}: {exc}") from None
            except DecimalException:
                raise StatementParseError(
                    f"OFX transaction row {row_number}: amount out of range"
                ) from None

    dates = [txn.posted_date for txn in rows.txns]
    newest_first = bool(dates and dates[0] > dates[-1] and dates == sorted(dates, reverse=True))
    opening, reconciliation = rows.reconcile(closing, newest_first=newest_first)
    period_start = _date(start) if start else None
    period_end = _date(end) if end else None
    if period_start and period_end and period_start > period_end:
        raise StatementParseError("invalid OFX statement period")
    return OfxStatement(
        txns=tuple(rows.txns),
        reconciliation=reconciliation,
        opening_balance=opening,
        closing_balance=closing,
        account_last4=account[-4:],
        currency=currency,
        period_start=period_start,
        period_end=period_end,
    )


def parse_ofx(source: str | bytes) -> tuple[OfxStatement, ...]:
    """Return one export per bank or card statement. Duplicate FITIDs fail within a statement."""
    root = _tree(_decode(source))
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1].upper()
    if root.tag != "OFX":
        raise StatementParseError("OFX root not found")
    statements = tuple(
        _statement(element) for element in root.iter() if element.tag in {"STMTRS", "CCSTMTRS"}
    )
    if not statements:
        raise StatementParseError("OFX contains no bank or credit-card statements")
    return statements
