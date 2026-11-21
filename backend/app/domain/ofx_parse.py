"""Parse bank and credit-card statements from OFX 1.x SGML or OFX 2.x XML/QFX."""

import codecs
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException
from xml.etree import ElementTree as ET

from app.domain.statement_parse import StatementParseError
from app.domain.tabular_statement import ExportRows, TransactionExport, resolve_encoding

_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_OFX_ROOT = re.compile(r"<OFX\s*>", re.IGNORECASE)
_SGML_LEAF = re.compile(r"<([A-Z][A-Z0-9_.-]*)>([^<]*)", re.IGNORECASE)
_BARE_AMP = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)")
_DATE = re.compile(r"([0-9]{4})([0-9]{2})([0-9]{2})(?:[0-9]{6}(?:\.[0-9]+)?)?(?:\[[^\[\]]+\])?")
# At most 12 integer digits and 2 decimals, the same bounds as MoneyStr / NUMERIC(14,2).
_AMOUNT = re.compile(r"[+-]?(?:[0-9]{1,12}(?:\.[0-9]{0,2})?|\.[0-9]{1,2})")
_CLOSER = re.compile(r"</([A-Z][A-Z0-9_.-]*)>")
# Only this much of a file is searched for its encoding. Every pattern below is anchored or made of
# non-overlapping, length-bounded classes, so the scan is linear in these bytes, whatever follows.
_SNIFF_BYTES = 1024
_XML_DECL = re.compile(rb"[ \t\r\n]*<\?xml\b")
_XML_ENCODING = re.compile(
    rb"""encoding[ \t\r\n]{0,8}=[ \t\r\n]{0,8}['"]([A-Za-z0-9._-]{1,40})['"]"""
)
_CHARSET = re.compile(rb"(?im)^CHARSET:[ \t]{0,8}([A-Za-z0-9_-]{1,40})[ \t]*\r?$")


@dataclass(frozen=True, kw_only=True)
class OfxStatement(TransactionExport):
    account_last4: str
    currency: str
    period_start: date | None
    period_end: date | None
    ledger_as_of: date | None = None  # LEDGERBAL DTASOF: when the closing balance was struck


def _decode(source: str | bytes) -> str:
    if isinstance(source, str):
        return source.removeprefix("\ufeff")
    encoding = "utf-8-sig"
    if source.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif source.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        head = source[:_SNIFF_BYTES]
        name: str | None = None
        charset = _CHARSET.search(head.split(b"<", 1)[0])
        if charset:
            name = charset[1].decode("ascii")
            if name.lower() == "none":
                name = "utf-8"
        elif _XML_DECL.match(head) and not source.startswith(codecs.BOM_UTF8):
            declaration = _XML_ENCODING.search(head[: max(head.find(b"?>"), 0)])
            if declaration:
                name = declaration[1].decode("ascii")
        if name is not None:
            resolved = resolve_encoding(name)
            if resolved is None:
                raise StatementParseError("OFX cannot be decoded")
            encoding = resolved
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
        except (ET.ParseError, UnicodeError):
            if root is None or root.start() != 0:
                raise StatementParseError("malformed OFX XML") from None

    # OFX 1.x omits scalar end tags. Aggregate tags still require an explicit end tag.
    body = text[root.start() :] if root else text
    # An aggregate always has an end tag in OFX 1.x; a start tag with no value and no end tag
    # anywhere is an empty leaf (``<CHECKNUM>``) and must be closed too.
    aggregates = set(_CLOSER.findall(body.upper()))

    def close_leaf(match: re.Match[str]) -> str:
        tag, value = match.groups()
        if not value.strip() and tag.upper() in aggregates:
            return match[0]
        value = _BARE_AMP.sub("&amp;", value)
        if body[match.end() : match.end() + len(tag) + 3].upper() == f"</{tag.upper()}>":
            return f"<{tag}>{value}"
        return f"<{tag}>{value}</{tag}>"

    try:
        return ET.fromstring(_SGML_LEAF.sub(close_leaf, body))  # noqa: S314 - checked above
    except ET.ParseError as exc:
        raise StatementParseError(f"malformed OFX near line {exc.position[0]}") from None
    except UnicodeError:
        raise StatementParseError("OFX cannot be decoded") from None


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


def _statement(
    element: ET.Element, *, investments_skipped: bool, opening_balance: Decimal | None
) -> OfxStatement:
    account_tag = "BANKACCTFROM" if element.tag == "STMTRS" else "CCACCTFROM"
    account = _value(element, f"{account_tag}/ACCTID", required=True)
    currency = _value(element, "CURDEF", required=True).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise StatementParseError("invalid OFX currency")

    transaction_list = element.find("BANKTRANLIST")
    ledger = element.find("LEDGERBAL")
    closing = None if ledger is None else _amount(_value(ledger, "BALAMT", required=True))
    as_of_text = "" if ledger is None else _value(ledger, "DTASOF")
    ledger_as_of = _date(as_of_text) if as_of_text else None
    start = end = ""
    rows = ExportRows()
    if investments_skipped:
        rows.flag("investment_data_skipped")
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
                    # Both rows are kept (the ordinal separates them); a person decides.
                    rows.flag("duplicate_fitid")
                seen.add(fitid)
                if txn.find("CORRECTFITID") is not None or txn.find("CORRECTACTION") is not None:
                    # A correction replaces or deletes an earlier row. Applying that needs the
                    # earlier row, so never import it as an ordinary new transaction silently.
                    rows.flag("ofx_correction")
                description = _value(txn, "NAME") or _value(txn, "MEMO")
                rows.add(posted, description, amount, _balance(txn), fitid)
            except StatementParseError as exc:
                raise StatementParseError(f"OFX transaction row {row_number}: {exc}") from None
            except DecimalException:
                raise StatementParseError(
                    f"OFX transaction row {row_number}: amount out of range"
                ) from None

    dates = [txn.posted_date for txn in rows.txns]
    newest_first = bool(dates and dates[0] > dates[-1] and dates == sorted(dates, reverse=True))
    period_start = _date(start) if start else None
    period_end = _date(end) if end else None
    if period_start and period_end and period_start > period_end:
        raise StatementParseError("invalid OFX statement period")
    late = ledger_as_of is not None and period_end is not None and ledger_as_of > period_end
    opening, reconciliation = rows.reconcile(
        closing,
        newest_first=newest_first,
        opening=opening_balance,
        # A balance struck after the statement ends includes later activity: say that, not
        # a plain mismatch.
        mismatch_reason="ledger_balance_after_period_end" if late else "balance_mismatch",
    )
    return OfxStatement(
        txns=tuple(rows.txns),
        reconciliation=reconciliation,
        opening_balance=opening,
        closing_balance=closing,
        account_last4=account[-4:],
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        ledger_as_of=ledger_as_of,
    )


def _check_status(root: ET.Element) -> None:
    """A STATUS with SEVERITY ERROR (sign-on or statement level) means the file is not data."""
    for element in root.iter():
        if element.tag == "STATUS" and _value(element, "SEVERITY").upper() == "ERROR":
            raise StatementParseError("OFX reports an error status")


def parse_ofx(
    source: str | bytes, *, opening_balance: Decimal | None = None
) -> tuple[OfxStatement, ...]:
    """Return one export per bank or card statement.

    Real OFX carries a closing balance only, so the result is ``needs_review`` /
    ``missing_balance`` unless the caller knows the opening balance (the prior statement's
    closing) and passes it, which is allowed for a single-statement file. Duplicate FITIDs,
    correction rows and skipped investment data make the statement ``needs_review`` with a named
    reason instead of failing the file or dropping data silently.
    """
    root = _tree(_decode(source))
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1].upper()
    if root.tag != "OFX":
        raise StatementParseError("OFX root not found")
    _check_status(root)
    elements = [e for e in root.iter() if e.tag in {"STMTRS", "CCSTMTRS"}]
    if not elements:
        raise StatementParseError("OFX contains no bank or credit-card statements")
    if opening_balance is not None and len(elements) != 1:
        raise StatementParseError("an opening balance applies to a single-statement file")
    investments = any(e.tag == "INVSTMTRS" for e in root.iter())
    return tuple(
        _statement(e, investments_skipped=investments, opening_balance=opening_balance)
        for e in elements
    )
