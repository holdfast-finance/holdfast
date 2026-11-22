"""Model metadata rules that need no database: money precision, no floats, no secrets in clear."""

from __future__ import annotations

import re
from typing import Any

import pytest
from sqlalchemy import CheckConstraint, Float, Numeric

import app.models  # noqa: F401  (registers every table)
from app.db import Base
from app.models._common import is_valid_mask
from tests.models.conftest import CONTRACT_TABLES

SECRETISH = re.compile(r"(^|_)(token|secret|password)(_|$)")

# Every Numeric column in the schema, by kind. A new Numeric column fails until it's listed
# here with the right kind, so nobody adds an unreviewed NUMERIC (or one with the wrong scale).
NUMERIC_KINDS: dict[str, tuple[int, int]] = {
    "money": (14, 2),
    "quantity": (28, 10),
    "price": (18, 8),
    "ratio": (5, 4),
    "rate": (7, 4),
}
NUMERIC_COLUMNS: dict[tuple[str, str], str] = {
    ("transaction", "amount"): "money",
    ("transaction_split", "amount"): "money",
    ("balance_snapshot", "current"): "money",
    ("balance_snapshot", "available"): "money",
    ("balance_snapshot", "limit_amt"): "money",
    ("holding", "quantity"): "quantity",
    ("holding", "cost_basis"): "money",
    ("holding", "value"): "money",
    ("investment_txn", "quantity"): "quantity",
    ("investment_txn", "price"): "price",
    ("investment_txn", "amount"): "money",
    ("investment_txn", "fees"): "money",
    ("liability", "apr"): "rate",
    ("liability", "min_payment"): "money",
    ("liability", "statement_balance"): "money",
    ("liability", "original_principal"): "money",
    ("budget_line", "amount"): "money",
    ("budget_line", "alert_pct"): "ratio",
    ("tradeline", "credit_limit"): "money",
    ("tradeline", "balance"): "money",
    ("tradeline", "high_balance"): "money",
    ("parsed_field", "confidence"): "ratio",
    ("tax_item", "amount"): "money",
    ("tax_item", "confidence"): "ratio",
    ("estimate_snapshot", "federal_liability"): "money",
    ("estimate_snapshot", "state_liability"): "money",
    ("estimate_snapshot", "withheld"): "money",
    ("estimate_snapshot", "projected_due"): "money",
    ("agent_finding", "est_savings"): "money",
    ("import_run", "opening_balance"): "money",
    ("import_run", "closing_balance"): "money",
    ("import_run", "txn_sum"): "money",
    ("import_run", "balance_diff"): "money",
}


def _numeric_columns() -> dict[tuple[str, str], Numeric[Any]]:
    return {
        (t.name, c.name): c.type
        for t in Base.metadata.tables.values()
        for c in t.c
        if isinstance(c.type, Numeric)
    }


def test_metadata_matches_contract_table_list() -> None:
    assert set(Base.metadata.tables) == set(CONTRACT_TABLES)


def test_every_numeric_column_is_classified() -> None:
    found = _numeric_columns()
    assert set(found) - set(NUMERIC_COLUMNS) == set(), "unclassified Numeric column(s)"
    assert set(NUMERIC_COLUMNS) - set(found) == set(), "allow-map lists a missing column"


@pytest.mark.parametrize(("key", "kind"), sorted(NUMERIC_COLUMNS.items()))
def test_numeric_precision_matches_kind(key: tuple[str, str], kind: str) -> None:
    column_type = _numeric_columns()[key]
    assert not isinstance(column_type, Float)
    assert column_type.asdecimal is True
    assert (column_type.precision, column_type.scale) == NUMERIC_KINDS[kind], key


@pytest.mark.parametrize("key", sorted(k for k, kind in NUMERIC_COLUMNS.items() if kind == "ratio"))
def test_every_ratio_has_a_range_check(key: tuple[str, str]) -> None:
    tbl, col = key
    checks = {
        str(c.sqltext)
        for c in Base.metadata.tables[tbl].constraints
        if isinstance(c, CheckConstraint)
    }
    assert f"{col} IS NULL OR {col} BETWEEN 0 AND 1" in checks


def test_no_float_columns_anywhere() -> None:
    for t in Base.metadata.tables.values():
        for c in t.c:
            assert not isinstance(c.type, Float), f"{t.name}.{c.name}"


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        (None, True),
        ("0000", True),
        ("XXXX-0000", True),
        ("", True),
        ("00000", False),
        ("4000-0000-0000-0002", False),
        ("0 0 0 0 0", False),
    ],
)
def test_is_valid_mask(value: str | None, ok: bool) -> None:
    assert is_valid_mask(value) is ok


def test_secrets_are_only_stored_encrypted_or_hashed() -> None:
    """Token / key / secret columns are bytea ciphertext or hashes, never plain text names."""
    for t in Base.metadata.tables.values():
        for c in t.c:
            name = c.name
            if SECRETISH.search(name):
                assert name.endswith(("_enc", "_hash")), f"{t.name}.{name}"
