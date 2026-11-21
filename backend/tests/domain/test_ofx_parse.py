from datetime import date
from decimal import Decimal

import pytest

from app.domain.ofx_parse import parse_ofx
from app.domain.statement_parse import StatementParseError

SGML = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
ENCODING:USASCII
CHARSET:1252

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS>
<CURDEF>USD
<BANKACCTFROM><ACCTID>EXAMPLE0000
</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260101<DTEND>20260131
<STMTTRN><DTPOSTED>20260102140000.000[-5:EST]<TRNAMT>-3
<FITID>example-debit<NAME>Café & Books<BALAMT>7.00
</STMTTRN>
<STMTTRN><DTPOSTED>20260103<TRNAMT>+2.50<FITID>example-credit<MEMO>Refund
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>9.50<DTASOF>20260131</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>"""

XML = """<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="211"?>
<OFX>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><CCSTMTRS>
<CURDEF>CAD</CURDEF>
<CCACCTFROM><ACCTID>ABCDEF0001</ACCTID></CCACCTFROM>
<BANKTRANLIST><DTSTART>20260201</DTSTART><DTEND>20260228</DTEND>
<STMTTRN><DTPOSTED>20260205</DTPOSTED><TRNAMT>-4.125</TRNAMT>
<FITID>example-card</FITID><NAME>Book &amp; Pen</NAME><MEMO>Other detail</MEMO>
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>-24.50</BALAMT><DTASOF>20260228</DTASOF></LEDGERBAL>
</CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1>
</OFX>"""


def test_sgml_bank_statement_reconciles_running_balance_to_ledger() -> None:
    source = SGML.encode("cp1252")
    (result,) = parse_ofx(source)
    assert parse_ofx(source) == (result,)
    assert result.account_last4 == "0000"
    assert "EXAMPLE0000" not in repr(result)
    assert result.currency == "USD"
    assert (result.period_start, result.period_end) == (date(2026, 1, 1), date(2026, 1, 31))
    debit, credit = result.txns
    assert debit.posted_date == date(2026, 1, 2)
    assert debit.description == "Café & Books"
    assert credit.description == "Refund"
    assert (debit.external_id, credit.external_id) == ("example-debit", "example-credit")
    assert (debit.amount, credit.amount) == (Decimal("-3"), Decimal("2.50"))
    assert all(isinstance(t.amount, Decimal) for t in result.txns)
    assert result.opening_balance == Decimal("10.00")
    assert result.closing_balance == Decimal("9.50")
    assert result.reconciliation.status == "reconciled"


def test_xml_card_ledger_without_opening_is_explicitly_unverified() -> None:
    (result,) = parse_ofx(XML.encode())
    assert result.account_last4 == "0001"
    assert result.currency == "CAD"
    (txn,) = result.txns
    assert txn.description == "Book & Pen"
    assert txn.amount == Decimal("-4.125")
    assert result.closing_balance == Decimal("-24.50")
    assert result.reconciliation.status == "needs_review"
    assert result.reconciliation.reason == "missing_balance"


@pytest.mark.parametrize(("closing", "status"), [("9.51", "reconciled"), ("9.52", "needs_review")])
def test_ofx_reconciliation_tolerance(closing: str, status: str) -> None:
    (result,) = parse_ofx(SGML.replace("9.50", closing))
    assert result.reconciliation.status == status
    assert result.reconciliation.diff == Decimal(closing) - Decimal("9.50")


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("<FITID>example-debit", "duplicate FITID"),
        ("<BADFIELD>example-credit", "missing OFX FITID"),
    ],
)
def test_invalid_ofx_transactions_report_row(replacement: str, message: str) -> None:
    with pytest.raises(StatementParseError, match=f"OFX transaction row 2: {message}"):
        parse_ofx(SGML.replace("<FITID>example-credit", replacement))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_ofx_rejects_entity_expansion(encoding: str) -> None:
    attack = XML.replace(
        "<OFX>",
        '<!DOCTYPE OFX [<!ENTITY word "example"><!ENTITY many "&word;&word;&word;">]><OFX>',
    ).replace("Book &amp; Pen", "&many;")
    with pytest.raises(StatementParseError, match="disallows DOCTYPE and ENTITY"):
        parse_ofx(attack.encode(encoding))


def test_subcent_transactions_rounding_to_same_cent_keep_distinct_fingerprints() -> None:
    second = (
        "<STMTTRN><DTPOSTED>20260205</DTPOSTED><TRNAMT>-4.124</TRNAMT>"
        "<FITID>example-card-2</FITID><NAME>Book &amp; Pen</NAME></STMTTRN>"
    )
    (result,) = parse_ofx(
        XML.replace("-4.125", "-4.121").replace("</BANKTRANLIST>", second + "</BANKTRANLIST>")
    )
    assert [txn.amount for txn in result.txns] == [Decimal("-4.121"), Decimal("-4.124")]
    assert [txn.ordinal for txn in result.txns] == [1, 2]
    assert len({txn.fingerprint for txn in result.txns}) == 2


def test_closing_row_balance_and_ledger_share_the_same_boundary() -> None:
    source = SGML.replace("<BALAMT>7.00", "").replace("<MEMO>Refund", "<MEMO>Refund<BALAMT>9.50")
    (result,) = parse_ofx(source)
    assert result.opening_balance is None
    assert result.closing_balance == Decimal("9.50")
    assert result.reconciliation.reason == "missing_balance"
