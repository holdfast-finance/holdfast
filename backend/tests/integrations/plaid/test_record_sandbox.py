"""R1: the sandbox recorder refuses outside sandbox and writes nothing person-like or secret."""

from __future__ import annotations

import importlib.util
import json
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "record_plaid_sandbox.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_plaid_sandbox", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rec = load()

RAW: dict[str, Any] = {
    "accounts": [
        {
            "account_id": "REAL-ACCT-ID-1",
            "name": "Jane Q Public Checking",
            "official_name": "Jane Public Premium",
            "mask": "9876",
            "type": "depository",
            "subtype": "checking",
            "owner": "Jane Public",
            "balances": {
                "current": Decimal("100.10"),
                "available": Decimal("90"),
                "limit": None,
                "iso_currency_code": "USD",
                "unofficial_currency_code": None,
            },
        }
    ],
    "added": [
        {
            "account_id": "REAL-ACCT-ID-1",
            "transaction_id": "REAL-TXN-1",
            "pending_transaction_id": None,
            "amount": Decimal("12.34"),
            "date": "2026-01-02",
            "authorized_date": None,
            "pending": False,
            "iso_currency_code": "USD",
            "name": "Jane Public Rent",
            "merchant_name": None,
            "original_description": "JANE PUBLIC RENT 123 MAIN ST",
            "counterparties": [{"name": "Jane Public"}],
            "location": {"address": "123 Main St", "lat": Decimal("1.5")},
            "payment_meta": {"payee": "Jane Public", "reference_number": "R-99"},
            "personal_finance_category": {
                "primary": "RENT",
                "detailed": "RENT_X",
                "confidence_level": "HIGH",
            },
        }
    ],
    "modified": [],
    "removed": [{"account_id": "REAL-ACCT-ID-1", "transaction_id": "REAL-TXN-0"}],
    "next_cursor": "REAL-CURSOR",
    "has_more": False,
    "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
    "request_id": "REAL-REQ",
    "item": {"item_id": "REAL-ITEM", "institution_id": "ins_1"},
    "access_token": "access-sandbox-REAL",
}


def test_refuses_unless_sandbox() -> None:
    for env in ({}, {"PLAID_ENV": "production"}, {"PLAID_ENV": "Sandbox"}):
        with pytest.raises(rec.RefusedError):
            rec.require_sandbox(env)
    rec.require_sandbox({"PLAID_ENV": "sandbox"})


def test_main_refuses_in_production_before_any_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = rec.main(
        ["--out", str(tmp_path / "o")],
        {"PLAID_ENV": "production", "PLAID_CLIENT_ID": "x", "PLAID_SECRET": "y"},
    )
    assert code == 2
    assert not (tmp_path / "o").exists()
    assert "refused" in capsys.readouterr().err


def test_sanitized_page_has_no_ids_tokens_or_person_text() -> None:
    page = rec.sanitize_page(RAW, rec.Pseudonyms(), 1, last=True)
    text = rec.dump(page)
    for canary in (
        "REAL",
        "Jane",
        "JANE",
        "Public",
        "Main",
        "9876",
        "access-",
        "R-99",
        "ins_1",
        "counterparties",
        "location",
        "payment_meta",
        "owner",
        "confidence",
    ):
        assert canary not in text
    data = json.loads(text, parse_float=Decimal)
    assert data["accounts"][0]["mask"] == "0000"
    assert set(data) == {
        "accounts",
        "added",
        "modified",
        "removed",
        "next_cursor",
        "has_more",
        "transactions_update_status",
        "request_id",
    }


def test_references_still_line_up_and_numbers_are_exact() -> None:
    page = rec.sanitize_page(RAW, rec.Pseudonyms(), 1, last=True)
    text = rec.dump(page)
    assert '"amount": 12.34' in text  # a bare number, not a string and not a float repr
    assert '"current": 100.10' in text
    assert page["added"][0]["account_id"] == page["accounts"][0]["account_id"]
    assert page["removed"][0]["account_id"] == page["accounts"][0]["account_id"]
    assert page["added"][0]["name"].startswith("Merchant ")


def test_script_has_no_float_and_reads_no_env_file() -> None:
    source = SCRIPT.read_text()
    assert "float(" not in source
    assert "dotenv" not in source
    assert '".env"' not in source and "'.env'" not in source
