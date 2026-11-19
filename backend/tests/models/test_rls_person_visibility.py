"""Per-person account visibility (PLAN 20.3), enforced by RLS for holdfast_app.

Household with two people. Alex owns a private, a summary_only and a shared account; there is
also a joint account (no owner, marked private, which joint overrides). Each account has a
transaction, split, holding, investment txn, liability and balance snapshot.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.exc import DBAPIError

from tests.models.conftest import (
    ACCOUNT_DETAIL_TABLES,
    _insert,
    _reference_ids,
    delete_household,
    set_ctx,
    table,
)

pytestmark = pytest.mark.services

KINDS = ("private", "summary_only", "shared", "joint")


@dataclass
class VisibilityHousehold:
    household_id: uuid.UUID
    alex: uuid.UUID
    sam: uuid.UUID
    accounts: dict[str, uuid.UUID]  # kind -> account id
    transactions: dict[str, uuid.UUID]  # kind -> transaction id


@pytest.fixture(scope="module")
def household(app_engine: Engine) -> Iterator[VisibilityHousehold]:
    with app_engine.begin() as c:
        hid = c.execute(text("SELECT create_household('Example Visibility')")).scalar_one()
        set_ctx(c, hid, wide=True)
        _, security_id = _reference_ids(c)
        h = {"household_id": hid}
        alex = _insert(c, "person", **h, display_name="Alex Example", filing_role="primary")
        sam = _insert(c, "person", **h, display_name="Sam Example", filing_role="spouse")
        accounts: dict[str, uuid.UUID] = {}
        transactions: dict[str, uuid.UUID] = {}
        for kind in KINDS:
            acct = _insert(
                c,
                "account",
                **h,
                owner_person_id=None if kind == "joint" else alex,
                visibility="private" if kind == "joint" else kind,
                source="manual",
                name=f"Example {kind}",
                type="investment",
            )
            accounts[kind] = acct
            a = {"account_id": acct}
            txn = _insert(
                c,
                "transaction",
                **h,
                **a,
                posted_date=date(2026, 3, 1),
                amount=Decimal("-10.00"),
                description_raw=f"EXAMPLE {kind}",
                source="manual",
            )
            transactions[kind] = txn
            _insert(c, "transaction_split", **h, transaction_id=txn, amount=Decimal("-10.00"))
            _insert(
                c,
                "holding",
                **h,
                **a,
                security_id=security_id,
                as_of_date=date(2026, 3, 1),
                quantity=Decimal("1.0000000000"),
            )
            _insert(
                c,
                "investment_txn",
                **h,
                **a,
                date=date(2026, 3, 1),
                type="cash",
                amount=Decimal("5.00"),
            )
            _insert(c, "liability", **h, **a, kind="other")
            _insert(
                c,
                "balance_snapshot",
                **h,
                **a,
                as_of_date=date(2026, 3, 1),
                current=Decimal("100.00"),
            )
    yield VisibilityHousehold(hid, alex, sam, accounts, transactions)
    delete_household(app_engine, hid)


def _visible_accounts(c: Connection, hh: VisibilityHousehold, name: str) -> set[str]:
    """Which accounts (by kind) have rows in ``name`` visible to the current context.

    Splits map through their transaction id without a join, so only the split policy decides.
    """
    t = table(name)
    if name == "transaction_split":
        by_id = {v: k for k, v in hh.transactions.items()}
        col = t.c.transaction_id
    else:
        by_id = {v: k for k, v in hh.accounts.items()}
        col = t.c.id if name == "account" else t.c.account_id
    return {by_id[i] for i in c.execute(select(col)).scalars().all()}


def test_owner_sees_everything(app_engine: Engine, household: VisibilityHousehold) -> None:
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.alex)
        for name in ("account", "balance_snapshot", *ACCOUNT_DETAIL_TABLES):
            assert _visible_accounts(c, household, name) == set(KINDS), name


def test_private_account_invisible_to_other_person(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        assert _visible_accounts(c, household, "account") == {"summary_only", "shared", "joint"}


def test_summary_only_shows_balances_not_details(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        assert _visible_accounts(c, household, "balance_snapshot") == {
            "summary_only",
            "shared",
            "joint",
        }
        for name in ACCOUNT_DETAIL_TABLES:
            assert _visible_accounts(c, household, name) == {"shared", "joint"}, name


def test_no_person_sees_no_private_accounts(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    """Household context without a person (fail closed for private data)."""
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, None)
        assert _visible_accounts(c, household, "account") == {"summary_only", "shared", "joint"}
        assert _visible_accounts(c, household, "transaction") == {"shared", "joint"}


def test_household_wide_jobs_see_everything(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, None, wide=True)
        for name in ("account", "balance_snapshot", *ACCOUNT_DETAIL_TABLES):
            assert _visible_accounts(c, household, name) == set(KINDS), name


@pytest.mark.parametrize("kind", ["private", "summary_only"])
def test_other_person_cannot_write_details(
    app_engine: Engine, household: VisibilityHousehold, kind: str
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        with pytest.raises(DBAPIError, match="row-level security"):
            c.execute(
                table("transaction")
                .insert()
                .values(
                    household_id=household.household_id,
                    account_id=household.accounts[kind],
                    posted_date=date(2026, 3, 2),
                    amount=Decimal("-1.00"),
                    description_raw="EXAMPLE",
                    source="manual",
                )
            )


def test_other_person_cannot_touch_hidden_rows(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    acct, txn = table("account"), table("transaction")
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        private = household.accounts["private"]
        summary = household.accounts["summary_only"]
        assert c.execute(acct.update().where(acct.c.id == private).values(name="x")).rowcount == 0
        assert c.execute(acct.delete().where(acct.c.id == private)).rowcount == 0
        assert c.execute(txn.delete().where(txn.c.account_id == summary)).rowcount == 0


def test_other_person_cannot_hide_a_shared_account(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    """Sam can't flip Alex's shared account to private (the new row would vanish from Sam)."""
    acct = table("account")
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        with pytest.raises(DBAPIError, match="row-level security"):
            c.execute(
                acct.update()
                .where(acct.c.id == household.accounts["shared"])
                .values(visibility="private")
            )
