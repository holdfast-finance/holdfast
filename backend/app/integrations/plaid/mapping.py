"""Plaid JSON to Holdfast's connector values (PHASE-2 contract 3.1, 6.1 row 11).

This module is the **only** place Plaid's sign is flipped. Plaid reports a transaction ``amount``
as positive for money leaving the account (a debit, a card purchase) and negative for money
arriving (a deposit, a card payment). Holdfast stores outflow negative, inflow positive, so
``amount`` is negated exactly once here, and nothing downstream may negate it again.

Balances follow the same rule from the other side: Plaid reports a credit or loan balance as the
positive amount owed, and Holdfast's ``current`` is negative when the balance is owed. A
depository or investment balance keeps its sign. ``available`` and ``limit`` pass through: Plaid's
credit ``available`` is ``limit - owed``, which already equals Holdfast's ``limit + current``.

Every number reaches this module as ``Decimal`` (the client parses with ``parse_float=Decimal``).
A ``float`` anywhere is a bug and raises. Nothing here logs: a Plaid body never reaches a log.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.domain.connector import (
    AccountType,
    ConnectorAccount,
    ConnectorBalance,
    ConnectorTxn,
)


class MappingError(ValueError):
    """A Plaid value cannot be represented. The message names a field, never a value."""


_ACCOUNT_TYPES: dict[str, AccountType] = {
    "depository": "depository",
    "credit": "credit",
    "loan": "loan",
    "investment": "investment",
    "brokerage": "investment",
    "other": "other",
}
# Plaid reports the amount owed as positive on these types.
_OWED_TYPES: frozenset[AccountType] = frozenset({"credit", "loan"})


def money(value: object, field: str) -> Decimal | None:
    """A Plaid number as ``Decimal``. ``None`` stays ``None``. Float, bool and text are refused."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise MappingError(f"{field} is not a decimal number")
    result = Decimal(value)
    if not result.is_finite():
        raise MappingError(f"{field} is not finite")
    return result


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _required_text(row: dict[str, Any], key: str) -> str:
    value = _text(row.get(key))
    if value is None:
        raise MappingError(f"{key} is missing")
    return value


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise MappingError(f"{field} is missing")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise MappingError(f"{field} is not a date") from None


def account_type(plaid_type: object) -> AccountType:
    if isinstance(plaid_type, str) and plaid_type in _ACCOUNT_TYPES:
        return _ACCOUNT_TYPES[plaid_type]
    return "other"


def map_account(row: dict[str, Any]) -> ConnectorAccount:
    balances = row.get("balances")
    currency = _text(balances.get("iso_currency_code")) if isinstance(balances, dict) else None
    return ConnectorAccount(
        external_account_id=_required_text(row, "account_id"),
        name=_text(row.get("name")) or "Account",
        official_name=_text(row.get("official_name")),
        mask=_text(row.get("mask")),
        type=account_type(row.get("type")),
        subtype=_text(row.get("subtype")),
        currency=currency,
        institution_name=None,
    )


def map_balance(row: dict[str, Any], as_of: date) -> ConnectorBalance | None:
    """The account's balance in Holdfast's sign, or ``None`` when Plaid sent no balances."""
    balances = row.get("balances")
    if not isinstance(balances, dict):
        return None
    current = money(balances.get("current"), "current")
    if current is not None and account_type(row.get("type")) in _OWED_TYPES:
        current = -current  # owed (positive at Plaid) is negative here
    limit = money(balances.get("limit"), "limit")
    if limit is not None and limit < 0:
        raise MappingError("limit is negative")  # refuse, never turn it into "no limit"
    return ConnectorBalance(
        external_account_id=_required_text(row, "account_id"),
        as_of=as_of,
        current=current,
        available=money(balances.get("available"), "available"),
        limit=limit,
    )


def map_transaction(row: dict[str, Any]) -> ConnectorTxn:
    amount = money(row.get("amount"), "amount")
    if amount is None:
        raise MappingError("amount is missing")
    pending = row.get("pending")
    if not isinstance(pending, bool):
        raise MappingError("pending is missing")
    category = row.get("personal_finance_category")
    if not isinstance(category, dict):
        category = {}
    authorized = row.get("authorized_date")
    description = (
        _text(row.get("original_description"))
        or _text(row.get("name"))
        or _text(row.get("merchant_name"))
        or ""
    )
    return ConnectorTxn(
        external_account_id=_required_text(row, "account_id"),
        external_id=_required_text(row, "transaction_id"),
        # Plaid can send a null ``date``. The authorized date is the nearest honest stand-in.
        posted_date=_date(row.get("date") or row.get("authorized_date"), "date"),
        authorized_date=_date(authorized, "authorized_date") if authorized is not None else None,
        amount=-amount,  # the one and only sign flip (outflow negative)
        description_raw=description,
        merchant_name=_text(row.get("merchant_name")),
        pfc_primary=_text(category.get("primary")),
        pfc_detailed=_text(category.get("detailed")),
        is_pending=pending,
        pending_external_id=None if pending else _text(row.get("pending_transaction_id")),
        currency=_text(row.get("iso_currency_code")),
    )


def map_removed(row: dict[str, Any]) -> tuple[str, str]:
    """A removal key is ``(external_account_id, external_id)``."""
    return (_required_text(row, "account_id"), _required_text(row, "transaction_id"))


def check_currency(row: dict[str, Any], account_currency: str | None) -> None:
    """Refuse a transaction whose currency cannot be verified against its account.

    A row with no currency code at all passes (the account's currency applies). A row that
    carries any code, ISO or unofficial, needs an account whose currency is known and equal to
    it. An unofficial currency (crypto, points) is never accepted. ``account_currency`` is
    ``None`` when the account is absent from every page or reports no currency.
    """
    iso = _text(row.get("iso_currency_code"))
    unofficial = _text(row.get("unofficial_currency_code"))
    if iso is None and unofficial is None:
        return
    if iso is None:
        raise MappingError("transaction is in an unofficial currency")
    if account_currency is None:
        raise MappingError("transaction currency cannot be checked against its account")
    if iso != account_currency:
        raise MappingError("transaction currency differs from its account")
