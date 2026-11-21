from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from app.domain.csv_parse import csv_profile_from_mapping, parse_csv
from app.domain.fingerprint import import_fingerprint
from app.domain.statement_parse import StatementParseError

PROFILE = {"date": "When", "description": "What", "amount": "Value", "date_format": "%Y-%m-%d"}
INDEXED = dict(PROFILE, date=0, description=1, amount=2)


def test_named_csv_detects_header_and_reconciles_quoted_balances() -> None:
    profile = csv_profile_from_mapping(dict(PROFILE, balance="Balance"))
    source = (
        "\ufeffFictional export\r\n\r\nWhen,What,Value,Balance\r\n"
        '2026-02-01,"Market, local","(1,234.50)",15.50\r\n'
        '2026-02-02,"Refund\r\n counter",+4.50,20.00\r\n'
        "2026-02-03,Parking,2.00-,18.00\r\n"
    )
    result = parse_csv(source.encode(), profile)
    assert [t.amount for t in result.txns] == [
        Decimal("-1234.50"),
        Decimal("4.50"),
        Decimal("-2.00"),
    ]
    assert [t.description for t in result.txns] == ["Market, local", "Refund counter", "Parking"]
    assert result.opening_balance == Decimal("1250.00")
    assert result.closing_balance == Decimal("18.00")
    assert result.reconciliation.status == "reconciled"


def test_indexed_csv_uses_encoding_delimiter_and_inversion() -> None:
    profile = csv_profile_from_mapping(
        dict(INDEXED, delimiter=";", encoding="cp1252", sign="inverted", date_format="%d/%m/%Y")
    )
    result = parse_csv("05/02/2026;Café;$2.00\n06/02/2026;Refund;-3.00".encode("cp1252"), profile)
    first, second = result.txns
    assert first.posted_date == date(2026, 2, 5)
    assert first.description == "Café"
    assert first.amount == Decimal("-2.00")
    assert second.amount == Decimal("3.00")
    assert result.reconciliation.reason == "missing_balance"


def test_split_columns_and_explicit_indexed_header() -> None:
    profile = csv_profile_from_mapping(
        {**INDEXED, "amount": None, "debit": 2, "credit": 3, "header": "present"}
    )
    result = parse_csv(
        "D,Text,Spent,Received\n2026-01-01,Store,(4.00),\n2026-01-02,Pay,,10.00".encode("utf-16"),
        profile,
    )
    assert [item.amount for item in result.txns] == [Decimal("-4.00"), Decimal("10.00")]


def test_newest_first_running_balance_mismatch_is_flagged() -> None:
    profile = csv_profile_from_mapping(dict(PROFILE, balance="Balance", newest_first=True))
    result = parse_csv(
        "When,What,Value,Balance\n2026-02-02,Refund,2.00,8.02\n2026-02-01,Store,-4.00,6.00",
        profile,
    )
    assert result.opening_balance == Decimal("10.00")
    assert result.closing_balance == Decimal("8.02")
    assert result.reconciliation.reason == "balance_mismatch"
    assert result.reconciliation.diff == Decimal("0.02")


def test_one_running_balance_does_not_verify_itself() -> None:
    profile = csv_profile_from_mapping(dict(INDEXED, balance=3))
    result = parse_csv("2026-01-01,Store,-4.00,6.00", profile)
    assert result.closing_balance == Decimal("6.00")
    assert result.reconciliation.reason == "missing_balance"


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("tomorrow,Store,2.00", "unreadable date"),
        ("2026-01-01,,2.00", "missing description"),
        ("2026-01-01,Store,NaN", "unreadable amount"),
        ("2026-01-01,Store", "missing column"),
        ('2026-01-01,"Store,2.00', "invalid CSV quoting"),
    ],
)
def test_bad_rows_report_physical_line(bad: str, message: str) -> None:
    with pytest.raises(StatementParseError, match=f"CSV row 4: {message}"):
        parse_csv(
            f'When,What,Value\n2026-01-01,"A\nshop",1.00\n{bad}', csv_profile_from_mapping(PROFILE)
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"date": True}, "zero-based index"),
        ({"amount": None, "debit": 3}, "either amount or both debit and credit"),
        ({"amount": "When"}, "columns must be distinct"),
        ({"sign": "positive"}, "sign must be"),
        (
            {"amount": None, "debit": 2, "credit": 3, "sign": "inverted"},
            "inverted sign requires an amount column",
        ),
        ({"header": "absent"}, "column names require a header"),
        ({"date_format": "%m/%d"}, "complete date"),
        ({"delimiter": "::"}, "delimiter must be"),
        ({"encoding": "invented"}, "text encoding"),
    ],
)
def test_invalid_profiles(override: dict[str, object], message: str) -> None:
    with pytest.raises(StatementParseError, match=message):
        csv_profile_from_mapping({**PROFILE, **override})


def test_generic_example_profile() -> None:
    path = Path(__file__).resolve().parents[2] / "app/importers/csv_profiles/generic.yaml"
    profile = csv_profile_from_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert profile.date == "Example Posting Date"


def test_repeated_exports_have_stable_distinct_fingerprints() -> None:
    profile = csv_profile_from_mapping(INDEXED)
    for count in range(1, 30):
        body = "\n".join(["2026-01-01,Example Shop,2.00-"] * count)
        first, second = parse_csv(body, profile), parse_csv(body, profile)
        assert first == second
        fingerprints = [t.fingerprint for t in first.txns]
        assert fingerprints == [
            import_fingerprint(date(2026, 1, 1), Decimal("-2.00"), "Example Shop", ordinal)
            for ordinal in range(1, count + 1)
        ]
        assert len(set(fingerprints)) == count
        assert all(isinstance(t.amount, Decimal) for t in first.txns)
