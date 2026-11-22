from decimal import Decimal

import pytest

from app.domain.csv_parse import csv_profile_from_mapping, parse_csv
from app.domain.ofx_parse import MAX_OFX_TAGS, parse_ofx
from app.domain.statement_parse import StatementParseError
from app.importers.export_statement import MAX_ROWS, csv_statement

KEY = b"synthetic-test-key-0123456789"
PROFILE = csv_profile_from_mapping(
    {"date": "When", "description": "What", "amount": "Value", "date_format": "%Y-%m-%d"}
)


def test_control_characters_in_a_description_become_spaces() -> None:
    source = "When,What,Value\n2026-03-01,Ex\x01am\x7fple\x85 Shop,-1.00\n"
    (txn,) = parse_csv(source, PROFILE).txns
    assert txn.description == "Ex am ple Shop"
    assert txn.amount == Decimal("-1.00")


def test_a_csv_over_the_row_cap_is_refused() -> None:
    body = "When,What,Value\n" + "2026-03-01,Example Shop,-1.00\n" * (MAX_ROWS + 1)
    with pytest.raises(StatementParseError):
        csv_statement(body.encode(), [("csv-t", PROFILE)])
    ok = "When,What,Value\n" + "2026-03-01,Example Shop,-1.00\n" * 5
    assert csv_statement(ok.encode(), [("csv-t", PROFILE)]) is not None


def test_an_ofx_with_too_many_tags_is_refused_before_parsing() -> None:
    source = "OFXHEADER:100\n\n<OFX>" + "<A>" * (MAX_OFX_TAGS + 1) + "</OFX>"
    with pytest.raises(StatementParseError, match="too many"):
        parse_ofx(source, id_key=KEY)


def test_ofx_account_token_covers_kind_bank_id_and_the_full_account_number() -> None:
    def statement(bank_id: str, acct: str) -> str:
        return (
            "OFXHEADER:100\n\n<OFX><BANKMSGSRSV1><STMTTRNRS><STMTRS><CURDEF>USD\n"
            f"<BANKACCTFROM><BANKID>{bank_id}<ACCTID>{acct}</BANKACCTFROM>\n"
            "<BANKTRANLIST><DTSTART>20260101<DTEND>20260131\n"
            "<STMTTRN><DTPOSTED>20260102<TRNAMT>-3.00<FITID>f1<NAME>Example</STMTTRN>\n"
            "</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>"
        )

    tokens = {
        parse_ofx(statement(bank, acct), id_key=KEY)[0].account_token
        for bank, acct in (("111", "AAAA0000"), ("222", "AAAA0000"), ("111", "BBBB0000"))
    }
    assert len(tokens) == 3
    same = parse_ofx(statement("111", "AAAA0000"), id_key=KEY)[0]
    assert (
        same.account_token == parse_ofx(statement("111", "AAAA0000"), id_key=KEY)[0].account_token
    )
    assert "AAAA" not in same.account_token and len(same.account_token) == 32
