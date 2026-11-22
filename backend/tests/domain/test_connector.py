from datetime import date
from decimal import Decimal
from typing import cast

import pytest

from app.domain.connector import ConnectorBalance, ConnectorTxn

DAY = date(2026, 1, 15)


def _txn(amount: Decimal, pending_external_id: str | None = None) -> ConnectorTxn:
    return ConnectorTxn(
        external_account_id="account-example",
        external_id="pending-example",
        posted_date=DAY,
        authorized_date=None,
        amount=amount,
        description_raw="Example purchase",
        merchant_name=None,
        pfc_primary=None,
        pfc_detailed=None,
        is_pending=True,
        pending_external_id=pending_external_id,
    )


@pytest.mark.parametrize(
    ("invalid", "error"),
    [
        (1.5, TypeError),
        ("1.50", TypeError),
        (1, TypeError),
        (Decimal("1.001"), ValueError),
        (Decimal("1.500"), ValueError),
        (Decimal("NaN"), ValueError),
        (Decimal("Infinity"), ValueError),
    ],
)
def test_connector_amounts_require_finite_decimal_with_at_most_two_places(
    invalid: object, error: type[Exception]
) -> None:
    amount = cast(Decimal, invalid)
    with pytest.raises(error, match="amount must"):
        _txn(amount)

    for field in ("current", "available", "limit"):
        amounts: dict[str, Decimal | None] = dict(current=None, available=None, limit=None)
        amounts[field] = amount
        with pytest.raises(error, match=rf"{field} must"):
            ConnectorBalance("account-example", DAY, **amounts)


def test_pending_transaction_cannot_replace_another_pending_transaction() -> None:
    with pytest.raises(ValueError, match="pending_external_id"):
        _txn(Decimal("-8.25"), pending_external_id="older-pending")
