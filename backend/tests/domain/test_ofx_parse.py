import time
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
<STMTTRN><DTPOSTED>20260205</DTPOSTED><TRNAMT>-4.12</TRNAMT>
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
    assert txn.amount == Decimal("-4.12")
    assert result.closing_balance == Decimal("-24.50")
    assert result.reconciliation.status == "needs_review"
    assert result.reconciliation.reason == "missing_balance"


STANDARD = SGML.replace("<BALAMT>7.00", "")  # real OFX: a closing LEDGERBAL only


def test_real_ofx_has_closing_only_so_it_needs_review() -> None:
    (result,) = parse_ofx(STANDARD)
    assert result.opening_balance is None
    assert result.closing_balance == Decimal("9.50")
    assert (result.reconciliation.status, result.reconciliation.reason) == (
        "needs_review",
        "missing_balance",
    )


@pytest.mark.parametrize(("closing", "status"), [("9.51", "reconciled"), ("9.52", "needs_review")])
def test_ofx_reconciliation_tolerance_with_a_known_opening(closing: str, status: str) -> None:
    source = STANDARD.replace("9.50", closing)
    (result,) = parse_ofx(source, opening_balance=Decimal("10.00"))
    assert result.reconciliation.status == status
    assert result.reconciliation.diff == Decimal(closing) - Decimal("9.50")
    # The per-row BALAMT extension is a second, non-standard path to the same answer.
    (legacy,) = parse_ofx(SGML.replace("9.50", closing))
    assert legacy.reconciliation == result.reconciliation


def test_an_opening_balance_needs_exactly_one_statement() -> None:
    block = XML[XML.index("<CCSTMTTRNRS>") : XML.index("</CCSTMTTRNRS>") + len("</CCSTMTTRNRS>")]
    two = XML.replace(block, block + block.replace("ABCDEF0001", "ABCDEF0002"))
    assert len(parse_ofx(two)) == 2
    with pytest.raises(StatementParseError, match="single-statement"):
        parse_ofx(two, opening_balance=Decimal("1.00"))


def test_ledger_balance_struck_after_the_period_is_named() -> None:
    late = STANDARD.replace("<DTASOF>20260131", "<DTASOF>20260205").replace("9.50", "12.00")
    (result,) = parse_ofx(late, opening_balance=Decimal("10.00"))
    assert result.ledger_as_of == date(2026, 2, 5)
    assert result.reconciliation.reason == "ledger_balance_after_period_end"
    on_time = STANDARD.replace("9.50", "12.00")
    (plain,) = parse_ofx(on_time, opening_balance=Decimal("10.00"))
    assert plain.ledger_as_of == date(2026, 1, 31)
    assert plain.reconciliation.reason == "balance_mismatch"


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
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


@pytest.mark.parametrize("amount", ["-4.125", "1.001", "1234567890123.00", "1e3", "NaN", "--1"])
def test_ofx_amounts_are_bounded_like_csv(amount: str) -> None:
    with pytest.raises(StatementParseError, match="unreadable OFX amount"):
        parse_ofx(XML.replace("-4.12", amount))
    with pytest.raises(StatementParseError, match="unreadable OFX amount"):
        parse_ofx(XML.replace("-24.50", amount))
    ok = parse_ofx(XML.replace("-4.12", "123456789012.99"))
    assert ok[0].txns[0].amount == Decimal("123456789012.99")


def test_identical_rows_with_distinct_fitids_keep_distinct_fingerprints() -> None:
    second = (
        "<STMTTRN><DTPOSTED>20260205</DTPOSTED><TRNAMT>-4.12</TRNAMT>"
        "<FITID>example-card-2</FITID><NAME>Book &amp; Pen</NAME></STMTTRN>"
    )
    (result,) = parse_ofx(XML.replace("</BANKTRANLIST>", second + "</BANKTRANLIST>"))
    assert [txn.ordinal for txn in result.txns] == [1, 2]
    assert len({txn.fingerprint for txn in result.txns}) == 2


def test_duplicate_fitid_keeps_both_rows_and_needs_review() -> None:
    (result,) = parse_ofx(STANDARD.replace("<FITID>example-credit", "<FITID>example-debit"))
    assert len(result.txns) == 2
    assert len({t.fingerprint for t in result.txns}) == 2
    assert (result.reconciliation.status, result.reconciliation.reason) == (
        "needs_review",
        "duplicate_fitid",
    )


def test_correction_rows_need_review_with_a_named_reason() -> None:
    corrected = STANDARD.replace(
        "<MEMO>Refund", "<CORRECTFITID>example-debit<CORRECTACTION>REPLACE<MEMO>Refund"
    )
    (result,) = parse_ofx(corrected, opening_balance=Decimal("10.00"))
    assert len(result.txns) == 2
    assert (result.reconciliation.status, result.reconciliation.reason) == (
        "needs_review",
        "ofx_correction",
    )
    (clean,) = parse_ofx(STANDARD, opening_balance=Decimal("10.00"))
    assert clean.reconciliation.status == "reconciled"


def test_error_status_is_an_error_not_an_empty_statement() -> None:
    status = "<STATUS><CODE>2000<SEVERITY>ERROR</STATUS>"
    for marker in ("<STMTRS>", "<OFX>"):
        with pytest.raises(StatementParseError, match="error status"):
            parse_ofx(STANDARD.replace(marker, marker + status, 1))
    info = "<STATUS><CODE>0<SEVERITY>INFO</STATUS>"
    parse_ofx(STANDARD.replace("<STMTRS>", "<STMTRS>" + info, 1))


def test_mixed_investment_data_is_reported_as_skipped() -> None:
    mixed = STANDARD.replace(
        "</BANKMSGSRSV1>",
        "</BANKMSGSRSV1><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS><CURDEF>USD"
        "</INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1>",
    )
    (result,) = parse_ofx(mixed, opening_balance=Decimal("10.00"))
    assert (result.reconciliation.status, result.reconciliation.reason) == (
        "needs_review",
        "investment_data_skipped",
    )


def test_empty_unclosed_leaf_does_not_fail_the_file() -> None:
    source = STANDARD.replace("<FITID>example-debit", "<CHECKNUM><FITID>example-debit")
    source = source.replace("<MEMO>Refund", "<MEMO>Refund<SRVRTID>")
    (result,) = parse_ofx(source)
    assert len(result.txns) == 2


def test_fitid_digits_are_masked_but_stay_a_stable_key() -> None:
    def run(fitid: str) -> str | None:
        return parse_ofx(STANDARD.replace("example-debit", fitid))[0].txns[0].external_id

    a, b = run("4111111111111111-x"), run("4111111111111112-x")
    assert a and b and a != b and "4111111111111111" not in a
    assert a == run("4111111111111111-x")
    assert run("short-1234567") == "short-1234567"  # under 8 digits is left alone


def test_separated_card_numbers_are_masked_in_descriptions() -> None:
    source = STANDARD.replace("Café & Books", "Pay 4111-1111-1111-1111 or 4111 1111 1111 1111")
    (result,) = parse_ofx(source.encode("cp1252"))
    assert "1111" not in result.txns[0].description


@pytest.mark.parametrize(
    "source",
    [
        b"OFXHEADER:100\nDATA:OFXSGML\nCHARSET:utf-7\n\n<OFX></OFX>",
        b'<?xml version="1.0" encoding="utf-7"?><OFX></OFX>',
        b'<?xml version="1.0" encoding="rot13"?><OFX></OFX>',
        b"\xff\xfe\x00",
        b'<?xml version="1.0" encoding="ascii"?><OFX>\xc3\xa9</OFX>',
    ],
)
def test_encodings_are_allowlisted_and_failures_are_generic(source: bytes) -> None:
    with pytest.raises(StatementParseError, match="OFX"):
        parse_ofx(source)


def test_allowlisted_encodings_decode() -> None:
    for name, codec in (("1252", "cp1252"), ("8859-1", "iso-8859-1"), ("USASCII", "ascii")):
        source = STANDARD.replace("CHARSET:1252", f"CHARSET:{name}").replace("Café", "Cafe")
        assert len(parse_ofx(source.encode(codec))[0].txns) == 2


@pytest.mark.parametrize(
    "hostile",
    [
        b"<?xml" + b" " * 1_000_000,
        b"<?xml " + b"encoding" + b" " * 1_000_000,
        b"OFXHEADER:100\nCHARSET:" + b" " * 1_000_000,
        b"CHARSET:" + b"a" * 1_000_000,
        b"<?xml version='1.0' " + b"encoding=" * 100_000,
        b"<!" + b" " * 1_000_000,
        b"<OFX" + b" " * 1_000_000,
        b"<OFX>" + b"<A>" * 300_000,
        b"<OFX>" + b"&" * 1_000_000,
    ],
)
def test_hostile_input_fails_fast(hostile: bytes) -> None:
    started = time.perf_counter()
    with pytest.raises(StatementParseError):
        parse_ofx(hostile)
    assert time.perf_counter() - started < 1.0


def test_closing_row_balance_and_ledger_share_the_same_boundary() -> None:
    source = SGML.replace("<BALAMT>7.00", "").replace("<MEMO>Refund", "<MEMO>Refund<BALAMT>9.50")
    (result,) = parse_ofx(source)
    assert result.opening_balance is None
    assert result.closing_balance == Decimal("9.50")
    assert result.reconciliation.reason == "missing_balance"
