"""Per-person account visibility (PLAN 20.3), enforced by RLS for holdfast_app.

Household with two people. Alex owns a private, a summary_only and a shared account; there is
also a joint account (no owner, marked private, which joint overrides). Each account has a
transaction, split, holding, investment txn, liability and balance snapshot.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

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
def household(app_engine: Engine, owner_engine: Engine) -> Iterator[VisibilityHousehold]:
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
    delete_household(owner_engine, hid)


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


@contextmanager
def rolled_back(engine: Engine) -> Iterator[Connection]:
    """A transaction that is always rolled back, so write probes never change the fixture."""
    with engine.connect() as c:
        tx = c.begin()
        try:
            yield c
        finally:
            tx.rollback()


def _account_row(app_engine: Engine, hh: VisibilityHousehold, kind: str) -> tuple[str, Any]:
    acct = table("account")
    with app_engine.begin() as c:
        set_ctx(c, hh.household_id, None, wide=True)
        visibility, owner = c.execute(
            select(acct.c.visibility, acct.c.owner_person_id).where(acct.c.id == hh.accounts[kind])
        ).one()
        return visibility, owner


# Every way a non-owner could gain or steal access by rewriting a visible account (finding H1).
TAKEOVERS = [
    pytest.param({"visibility": "shared"}, id="widen-to-shared"),
    pytest.param({"owner_person_id": "SAM"}, id="claim-ownership"),
    pytest.param({"owner_person_id": None}, id="make-joint"),
    pytest.param({"visibility": "private", "owner_person_id": "SAM"}, id="steal-and-hide"),
    pytest.param({"visibility": "private"}, id="hide-from-owner"),
    pytest.param({"name": "renamed"}, id="plain-edit"),
]


@pytest.mark.parametrize("kind", ["summary_only", "shared"])
@pytest.mark.parametrize("values", TAKEOVERS)
def test_other_person_cannot_rewrite_a_visible_account(
    app_engine: Engine, household: VisibilityHousehold, kind: str, values: dict[str, Any]
) -> None:
    """Seeing an account (shared / summary_only) never grants the right to change it."""
    acct = table("account")
    vals = {k: household.sam if v == "SAM" else v for k, v in values.items()}
    before = _account_row(app_engine, household, kind)
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.sam)
        assert c.execute(select(acct.c.id).where(acct.c.id == household.accounts[kind])).one()
        upd = c.execute(acct.update().where(acct.c.id == household.accounts[kind]).values(**vals))
        assert upd.rowcount == 0
        assert c.execute(acct.delete().where(acct.c.id == household.accounts[kind])).rowcount == 0
    assert _account_row(app_engine, household, kind) == before


@pytest.mark.parametrize("owner", ["ALEX", "OTHER_HOUSEHOLD_PERSON"])
def test_cannot_insert_an_account_owned_by_someone_else(
    app_engine: Engine, household: VisibilityHousehold, owner: str
) -> None:
    owner_id = household.alex if owner == "ALEX" else uuid.uuid4()
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.sam)
        with pytest.raises(DBAPIError, match=r"row-level security|foreign key"):
            c.execute(
                table("account")
                .insert()
                .values(
                    household_id=household.household_id,
                    owner_person_id=owner_id,
                    visibility="private",
                    source="manual",
                    name="Example planted",
                    type="depository",
                )
            )


@pytest.mark.parametrize("owner", ["SAM", None])
def test_can_insert_own_or_joint_account(
    app_engine: Engine, household: VisibilityHousehold, owner: str | None
) -> None:
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.sam)
        c.execute(
            table("account")
            .insert()
            .values(
                household_id=household.household_id,
                owner_person_id=household.sam if owner else None,
                source="manual",
                name="Example new",
                type="depository",
            )
        )


def test_owner_can_change_visibility_but_not_hand_the_account_away(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    acct = table("account")
    target = acct.c.id == household.accounts["shared"]
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.alex)
        assert c.execute(acct.update().where(target).values(visibility="private")).rowcount == 1
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.alex)
        with pytest.raises(DBAPIError, match="row-level security"):
            c.execute(acct.update().where(target).values(owner_person_id=household.sam))
    with rolled_back(app_engine) as c:
        # Reassigning an account to another person is a household-wide (admin) operation.
        set_ctx(c, household.household_id, None, wide=True)
        assert c.execute(acct.update().where(target).values(owner_person_id=household.sam)).rowcount


def test_joint_account_is_writable_by_everyone(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    acct = table("account")
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.sam)
        upd = c.execute(
            acct.update().where(acct.c.id == household.accounts["joint"]).values(name="Ours")
        )
        assert upd.rowcount == 1


def test_ownership_guard_trigger_blocks_non_owner_even_without_rls(
    owner_engine: Engine, household: VisibilityHousehold
) -> None:
    """Defense in depth: with RLS lifted (inside a rolled-back transaction), the trigger alone
    still stops a non-owner from changing visibility or owner."""
    acct = table("account")
    target = acct.c.id == household.accounts["summary_only"]
    for values in ({"visibility": "shared"}, {"owner_person_id": household.sam}):
        with rolled_back(owner_engine) as c:
            c.execute(text("ALTER TABLE account NO FORCE ROW LEVEL SECURITY"))
            set_ctx(c, household.household_id, household.sam)
            with pytest.raises(DBAPIError, match="only the account owner"):
                c.execute(acct.update().where(target).values(**values))
    with rolled_back(owner_engine) as c:
        c.execute(text("ALTER TABLE account NO FORCE ROW LEVEL SECURITY"))
        set_ctx(c, household.household_id, household.sam)
        # Other columns aren't the trigger's business (RLS covers them).
        assert c.execute(acct.update().where(target).values(name="x")).rowcount == 1
    with rolled_back(owner_engine) as c:
        c.execute(text("ALTER TABLE account NO FORCE ROW LEVEL SECURITY"))
        set_ctx(c, household.household_id, None)  # no person: still not the owner
        with pytest.raises(DBAPIError, match="only the account owner"):
            c.execute(acct.update().where(target).values(visibility="shared"))


# --------------------------------------------------------------------------- detail writes

DETAIL_WRITE_TABLES = (*ACCOUNT_DETAIL_TABLES, "balance_snapshot")


def _detail_target(name: str, hh: VisibilityHousehold, kind: str) -> Any:
    t = table(name)
    if name == "transaction_split":
        return t.c.transaction_id == hh.transactions[kind]
    return t.c.account_id == hh.accounts[kind]


@pytest.mark.parametrize("name", DETAIL_WRITE_TABLES)
def test_other_person_cannot_edit_details_of_a_shared_account(
    app_engine: Engine, household: VisibilityHousehold, name: str
) -> None:
    """Shared means read-only for everyone but the owner: no edits, no deletes."""
    t = table(name)
    probe = {"notes": "x"} if name == "transaction" else {"household_id": household.household_id}
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.sam)
        target = _detail_target(name, household, "shared")
        if name != "balance_snapshot":  # summary-only rules: balances stay visible
            assert c.execute(select(t).where(target)).first() is not None
        assert c.execute(t.update().where(target).values(**probe)).rowcount == 0
        assert c.execute(t.delete().where(target)).rowcount == 0


@pytest.mark.parametrize("name", DETAIL_WRITE_TABLES)
def test_owner_and_joint_details_are_writable(
    app_engine: Engine, household: VisibilityHousehold, name: str
) -> None:
    t = table(name)
    for person, kind in ((household.alex, "shared"), (household.sam, "joint")):
        with rolled_back(app_engine) as c:
            set_ctx(c, household.household_id, person)
            target = _detail_target(name, household, kind)
            assert c.execute(t.delete().where(target)).rowcount >= 1, (name, kind)


def test_other_person_cannot_insert_details_into_a_shared_account(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    h = {"household_id": household.household_id}
    inserts = {
        "transaction": {
            **h,
            "account_id": household.accounts["shared"],
            "posted_date": date(2026, 3, 2),
            "amount": Decimal("-1.00"),
            "description_raw": "EXAMPLE",
            "source": "manual",
        },
        "balance_snapshot": {
            **h,
            "account_id": household.accounts["shared"],
            "as_of_date": date(2026, 3, 9),
            "current": Decimal("1.00"),
        },
        "transaction_split": {
            **h,
            "transaction_id": household.transactions["shared"],
            "amount": Decimal("-1.00"),
        },
    }
    for name, values in inserts.items():
        with rolled_back(app_engine) as c:
            set_ctx(c, household.household_id, household.sam)
            with pytest.raises(DBAPIError, match="row-level security"):
                c.execute(table(name).insert().values(**values))


def test_cannot_move_a_transaction_into_an_account_you_cannot_write(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    txn = table("transaction")
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.sam)
        with pytest.raises(DBAPIError, match="row-level security"):
            c.execute(
                txn.update()
                .where(txn.c.id == household.transactions["joint"])
                .values(account_id=household.accounts["shared"])
            )


def test_cannot_move_a_transaction_out_of_an_account_you_cannot_write(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    txn = table("transaction")
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.sam)
        moved = c.execute(
            txn.update()
            .where(txn.c.id == household.transactions["shared"])
            .values(account_id=household.accounts["joint"])
        )
        assert moved.rowcount == 0


def test_owner_can_move_between_writable_accounts(
    app_engine: Engine, household: VisibilityHousehold
) -> None:
    txn = table("transaction")
    with rolled_back(app_engine) as c:
        set_ctx(c, household.household_id, household.alex)
        moved = c.execute(
            txn.update()
            .where(txn.c.id == household.transactions["private"])
            .values(account_id=household.accounts["joint"])
        )
        assert moved.rowcount == 1
