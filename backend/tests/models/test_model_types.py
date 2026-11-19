"""Model metadata rules that need no database: money precision, no floats, no secrets in clear."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import Float, Numeric

import app.models  # noqa: F401  (registers every table)
from app.db import Base
from tests.models.conftest import CONTRACT_TABLES

SECRETISH = re.compile(r"(^|_)(token|secret|password)(_|$)")

MONEY_COLUMNS = {
    ("transaction", "amount"),
    ("transaction_split", "amount"),
    ("balance_snapshot", "current"),
    ("balance_snapshot", "available"),
    ("balance_snapshot", "limit_amt"),
    ("holding", "cost_basis"),
    ("holding", "value"),
    ("investment_txn", "amount"),
    ("investment_txn", "fees"),
    ("liability", "min_payment"),
    ("liability", "statement_balance"),
    ("liability", "original_principal"),
    ("budget_line", "amount"),
    ("tradeline", "credit_limit"),
    ("tradeline", "balance"),
    ("tradeline", "high_balance"),
    ("tax_item", "amount"),
    ("estimate_snapshot", "federal_liability"),
    ("estimate_snapshot", "state_liability"),
    ("estimate_snapshot", "withheld"),
    ("estimate_snapshot", "projected_due"),
    ("agent_finding", "est_savings"),
}


def test_metadata_matches_contract_table_list() -> None:
    assert set(Base.metadata.tables) == set(CONTRACT_TABLES)


@pytest.mark.parametrize(("tbl", "col"), sorted(MONEY_COLUMNS))
def test_money_is_numeric_14_2(tbl: str, col: str) -> None:
    column_type = Base.metadata.tables[tbl].c[col].type
    assert isinstance(column_type, Numeric)
    assert not isinstance(column_type, Float)
    assert (column_type.precision, column_type.scale) == (14, 2)
    assert column_type.asdecimal is True


def test_no_float_columns_anywhere() -> None:
    for t in Base.metadata.tables.values():
        for c in t.c:
            assert not isinstance(c.type, Float), f"{t.name}.{c.name}"


def test_quantities_and_prices_use_their_precision() -> None:
    tables = Base.metadata.tables
    for tbl, col in (("holding", "quantity"), ("investment_txn", "quantity")):
        typ = tables[tbl].c[col].type
        assert isinstance(typ, Numeric)
        assert (typ.precision, typ.scale) == (28, 10)
    price = tables["investment_txn"].c["price"].type
    assert isinstance(price, Numeric)
    assert (price.precision, price.scale) == (18, 8)


def test_secrets_are_only_stored_encrypted_or_hashed() -> None:
    """Token / key / secret columns are bytea ciphertext or hashes, never plain text names."""
    for t in Base.metadata.tables.values():
        for c in t.c:
            name = c.name
            if SECRETISH.search(name):
                assert name.endswith(("_enc", "_hash")), f"{t.name}.{name}"
