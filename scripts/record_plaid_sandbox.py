#!/usr/bin/env python3
"""R1: record Plaid **sandbox** /transactions/sync pages as sanitized fixtures.

Sandbox only. It refuses unless ``PLAID_ENV=sandbox`` (read from the process environment, never
from a file), and it never touches the slot ledger: sandbox Items use no Trial slot.

Run from ``backend/``::

    PLAID_ENV=sandbox PLAID_CLIENT_ID=... PLAID_SECRET=... \\
        uv run python ../scripts/record_plaid_sandbox.py --out tests/fixtures/plaid/recorded-1

``--out`` must resolve inside ``backend/tests/fixtures/plaid/`` (no ``..``, no symlinks), and
pages are opened exclusively, so an existing file is never overwritten.

Nothing raw is written. Each page is rebuilt from an allowlist of keys: ids become stable
pseudonyms, free text (names, merchants, descriptions) becomes ``MERCHANT n`` / ``Account n``,
masks become ``0000``, and every key not on the allowlist (counterparties, location,
payment_meta, account owner, logos, websites, tokens, request IDs) is dropped. Amounts and dates
stay: sandbox data is synthetic and they are what the sign tests need.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "plaid"
SANDBOX_INSTITUTION = "ins_109508"  # Plaid's "First Platypus Bank" test institution
MAX_PAGES = 20

_PFC_KEYS = ("primary", "detailed")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_PFC = re.compile(r"[A-Z_]{1,64}")
_TYPES = frozenset({"depository", "credit", "loan", "investment", "brokerage", "other"})
# Plaid's documented account subtypes. Anything else becomes "other".
_SUBTYPES = frozenset(
    {
        "checking", "savings", "cd", "money market", "hsa", "cash management", "ebt", "paypal",
        "prepaid", "credit card", "auto", "business", "commercial", "construction", "consumer",
        "home equity", "line of credit", "loan", "mortgage", "overdraft", "student", "personal",
        "401k", "403b", "457b", "529", "ira", "roth", "roth 401k", "sep ira", "simple ira",
        "pension", "retirement", "brokerage", "mutual fund", "crypto exchange", "isa",
        "cash isa", "other",
    }
)  # fmt: skip
_STATUS_VALUES = {"NOT_READY", "INITIAL_UPDATE_COMPLETE", "HISTORICAL_UPDATE_COMPLETE"}


class RefusedError(Exception):
    """The recorder will not run in this environment."""


def require_sandbox(env: Mapping[str, str]) -> None:
    if env.get("PLAID_ENV") != "sandbox":
        raise RefusedError("PLAID_ENV must be exactly 'sandbox'")


def resolve_out(out: Path, root: Path = FIXTURE_ROOT) -> Path:
    """``out`` as an absolute path strictly inside ``root``, with no symlink on the way."""
    base = root.resolve()
    target = out.resolve()  # a relative path resolves against the current directory
    lexical = Path(os.path.abspath(out))  # collapses ".." without following links
    if target == base or not target.is_relative_to(base) or not lexical.is_relative_to(base):
        raise RefusedError(f"--out must be inside {root}")
    # Every component below the root must be a plain directory (or not exist yet).
    probe = base
    for part in lexical.relative_to(base).parts:
        probe = probe / part
        if probe.is_symlink():
            raise RefusedError("--out must not be or pass through a symlink")
    return target


class Pseudonyms:
    """Stable replacements, so references between rows still line up after sanitizing."""

    def __init__(self) -> None:
        self._maps: dict[str, dict[str, str]] = {}

    def get(self, kind: str, value: str | None, template: str) -> str | None:
        if value is None:
            return None
        table = self._maps.setdefault(kind, {})
        if value not in table:
            table[value] = template.format(n=len(table) + 1)
        return table[value]


def _match(value: object, pattern: re.Pattern[str], placeholder: str) -> str:
    """Keep a string only when the whole of it matches ``pattern``."""
    if isinstance(value, str) and pattern.fullmatch(value):
        return value
    return placeholder


def _optional(value: object, pattern: re.Pattern[str], placeholder: str) -> str | None:
    return None if value is None else _match(value, pattern, placeholder)


def _number(value: object) -> Decimal | int | None:
    """A Plaid number, or ``None`` for anything else (so a string never rides in a money slot)."""
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    return value


def _txn(row: dict[str, Any], ps: Pseudonyms) -> dict[str, Any]:
    amount = _number(row.get("amount"))
    out: dict[str, Any] = {
        "amount": 0 if amount is None else amount,
        "date": _match(row.get("date"), _DATE, "1970-01-01"),
        "authorized_date": _optional(row.get("authorized_date"), _DATE, "1970-01-01"),
        "pending": row.get("pending") is True,
        "iso_currency_code": _optional(row.get("iso_currency_code"), _CURRENCY, "XXX"),
    }
    out["account_id"] = ps.get("account", row.get("account_id"), "acct-{n}")
    out["transaction_id"] = ps.get("txn", row.get("transaction_id"), "txn-{n:04d}")
    out["pending_transaction_id"] = ps.get("txn", row.get("pending_transaction_id"), "txn-{n:04d}")
    merchant = row.get("merchant_name")
    out["merchant_name"] = ps.get("merchant", merchant, "Merchant {n}")
    # The name and the raw description share one pseudonym namespace with the merchant.
    out["name"] = ps.get("merchant", row.get("name"), "Merchant {n}")
    original = row.get("original_description")
    out["original_description"] = ps.get("merchant", original, "Merchant {n}")
    category = row.get("personal_finance_category")
    out["personal_finance_category"] = (
        {k: _match(category.get(k), _PFC, "OTHER") for k in _PFC_KEYS if k in category}
        if isinstance(category, dict)
        else None
    )
    return out


def _removed(row: dict[str, Any], ps: Pseudonyms) -> dict[str, Any]:
    return {
        "account_id": ps.get("account", row.get("account_id"), "acct-{n}"),
        "transaction_id": ps.get("txn", row.get("transaction_id"), "txn-{n:04d}"),
    }


def _account(row: dict[str, Any], ps: Pseudonyms) -> dict[str, Any]:
    account_id = ps.get("account", row.get("account_id"), "acct-{n}")
    kind = row.get("type")
    subtype = row.get("subtype")
    out: dict[str, Any] = {
        "type": kind if isinstance(kind, str) and kind in _TYPES else "other",
        "subtype": subtype if isinstance(subtype, str) and subtype in _SUBTYPES else "other",
        "account_id": account_id,
        "name": f"Account {str(account_id).split('-')[-1]}",
        "official_name": None,
        "mask": "0000",
    }
    balances = row.get("balances")
    if isinstance(balances, dict):
        out["balances"] = {
            "current": _number(balances.get("current")),
            "available": _number(balances.get("available")),
            "limit": _number(balances.get("limit")),
            "iso_currency_code": _optional(balances.get("iso_currency_code"), _CURRENCY, "XXX"),
        }
    return out


def sanitize_page(page: dict[str, Any], ps: Pseudonyms, cursor_index: int) -> dict[str, Any]:
    """Rebuild one /transactions/sync response from allowlisted keys and strict patterns only.

    ``has_more`` is Plaid's own value, so a recording cut off by the page cap still says so.
    """
    status = page.get("transactions_update_status")
    return {
        "accounts": [_account(a, ps) for a in page.get("accounts") or [] if isinstance(a, dict)],
        "added": [_txn(r, ps) for r in page.get("added") or [] if isinstance(r, dict)],
        "modified": [_txn(r, ps) for r in page.get("modified") or [] if isinstance(r, dict)],
        "removed": [_removed(r, ps) for r in page.get("removed") or [] if isinstance(r, dict)],
        "next_cursor": f"cursor-{cursor_index}",
        "has_more": page.get("has_more") is True,
        "transactions_update_status": status if status in _STATUS_VALUES else "NOT_READY",
        "request_id": f"recorded-{cursor_index}",
    }


_MARK = re.compile(r'"@@(-?[0-9.]+)@@"')


def _decimal_marker(value: object) -> str:
    if isinstance(value, Decimal):
        return f"@@{value}@@"
    raise TypeError("unexpected type in a sanitized page")


def dump(value: object) -> str:
    """JSON with Decimal written as a bare number, never through a float."""
    text = json.dumps(value, indent=2, sort_keys=True, default=_decimal_marker)
    return _MARK.sub(r"\1", text) + "\n"


async def record(out_dir: Path, env: Mapping[str, str]) -> int:
    """Create a sandbox Item, page /transactions/sync, write sanitized pages. Returns pages."""
    require_sandbox(env)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.integrations.plaid.client import PlaidClient, PlaidCredentials
    from app.security.secret_token import SecretToken

    credentials = PlaidCredentials(
        env="sandbox",
        client_id=SecretToken(env["PLAID_CLIENT_ID"]),
        secret=SecretToken(env["PLAID_SECRET"]),
    )
    raw_pages: list[dict[str, Any]] = []
    async with PlaidClient(credentials) as plaid:
        created = await plaid.post(
            "/sandbox/public_token/create",
            {"institution_id": SANDBOX_INSTITUTION, "initial_products": ["transactions"]},
        )
        exchanged = await plaid.item_public_token_exchange(
            SecretToken(str(created["public_token"]))
        )
        try:
            cursor: str | None = None
            for _ in range(MAX_PAGES):
                body: dict[str, Any] = {
                    "access_token": exchanged.access_token.reveal(),
                    "count": 100,
                }
                if cursor:
                    body["cursor"] = cursor
                page = await plaid.post("/transactions/sync", body)
                raw_pages.append(page)
                cursor = page.get("next_cursor") or cursor
                if page.get("has_more") is not True:
                    break
        finally:
            # The sandbox Item is removed even when paging fails.
            await plaid.post("/item/remove", {"access_token": exchanged.access_token.reveal()})
    write_pages(out_dir, raw_pages, Pseudonyms())
    return len(raw_pages)


def write_pages(out_dir: Path, raw_pages: list[dict[str, Any]], ps: Pseudonyms) -> None:
    """Write sanitized pages. ``"x"`` mode: an existing file is an error, never overwritten."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(raw_pages, start=1):
        clean = sanitize_page(page, ps, index)
        with open(out_dir / f"sync_page{index}.json", "x", encoding="utf-8") as handle:
            handle.write(dump(clean))


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    environment = os.environ if env is None else env
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="a new directory for fixtures")
    args = parser.parse_args(argv)
    try:
        require_sandbox(environment)
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    for name in ("PLAID_CLIENT_ID", "PLAID_SECRET"):
        if not environment.get(name):
            print(f"refused: {name} is not set", file=sys.stderr)
            return 2
    try:
        out = resolve_out(args.out)
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if out.exists() and any(out.iterdir()):
        print("refused: --out must be empty or new", file=sys.stderr)
        return 2
    count = asyncio.run(record(out, environment))
    print(f"wrote {count} sanitized pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
