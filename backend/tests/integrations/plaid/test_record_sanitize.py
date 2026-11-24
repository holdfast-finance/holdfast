"""R1 round 2: free-text-capable fields are kept only when they match a strict pattern."""

from __future__ import annotations

import importlib.util
from decimal import Decimal
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "record_plaid_sandbox.py"
_spec = importlib.util.spec_from_file_location("record_plaid_sandbox", SCRIPT)
assert _spec is not None and _spec.loader is not None
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)

SECRET = "access-sandbox-SECRET"


def raw(**over: Any) -> dict[str, Any]:
    account = {
        "account_id": "a1",
        "type": "depository",
        "subtype": "checking",
        "balances": {"current": Decimal("1.00"), "iso_currency_code": "USD"},
    }
    txn = {
        "account_id": "a1",
        "transaction_id": "t1",
        "amount": Decimal("2.00"),
        "date": "2026-01-02",
        "authorized_date": "2026-01-01",
        "pending": False,
        "iso_currency_code": "USD",
        "personal_finance_category": {"primary": "INCOME", "detailed": "INCOME_WAGES"},
    }
    page = {
        "accounts": [account],
        "added": [txn],
        "modified": [],
        "removed": [],
        "next_cursor": "x",
        "has_more": False,
        "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE",
    }
    page.update(over)
    return page


def clean(page: dict[str, Any]) -> dict[str, Any]:
    return rec.sanitize_page(page, rec.Pseudonyms(), 1)  # type: ignore[no-any-return]


def test_well_formed_values_are_kept() -> None:
    out = clean(raw())
    assert out["accounts"][0]["type"] == "depository"
    assert out["accounts"][0]["subtype"] == "checking"
    assert out["accounts"][0]["balances"]["iso_currency_code"] == "USD"
    txn = out["added"][0]
    assert (txn["date"], txn["authorized_date"], txn["iso_currency_code"]) == (
        "2026-01-02",
        "2026-01-01",
        "USD",
    )
    assert txn["personal_finance_category"] == {"primary": "INCOME", "detailed": "INCOME_WAGES"}


def test_canary_text_in_enum_like_fields_never_survives() -> None:
    page = raw()
    page["accounts"][0]["subtype"] = "Jane Q Public"
    page["accounts"][0]["type"] = "Jane Q Public"
    page["accounts"][0]["balances"]["iso_currency_code"] = SECRET
    txn = page["added"][0]
    txn["iso_currency_code"] = SECRET
    txn["date"] = "Jane Q Public"
    txn["authorized_date"] = SECRET
    txn["personal_finance_category"] = {"primary": SECRET, "detailed": "Jane Q Public"}
    text = rec.dump(clean(page))
    for canary in ("Jane", "Public", "SECRET", "access-"):
        assert canary not in text


def test_replacements_are_valid_placeholders() -> None:
    page = raw()
    page["accounts"][0]["subtype"] = "Jane Q Public"
    page["added"][0]["iso_currency_code"] = SECRET
    page["added"][0]["date"] = SECRET
    out = clean(page)
    assert out["accounts"][0]["subtype"] == "other"
    assert out["added"][0]["iso_currency_code"] == "XXX"
    assert out["added"][0]["date"] == "1970-01-01"


def test_a_non_number_amount_is_not_copied() -> None:
    page = raw()
    page["added"][0]["amount"] = SECRET
    page["accounts"][0]["balances"]["current"] = SECRET
    text = rec.dump(clean(page))
    assert "SECRET" not in text


def test_a_last_page_that_still_has_more_stays_has_more() -> None:
    assert clean(raw(has_more=True))["has_more"] is True
    assert clean(raw(has_more=False))["has_more"] is False
    assert clean(raw(has_more="yes"))["has_more"] is False  # only a real True counts
