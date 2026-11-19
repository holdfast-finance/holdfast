"""Money and data-hygiene invariants the database enforces (ADR 0008).

Splits balance at commit, transfer groups imply transfers, magnitudes are non-negative, ratios
stay in [0, 1], masks hold at most four digits, SSN-shaped values never land in free text, and
names have sane lengths. All run as holdfast_app under RLS. All values are synthetic (SSN-shaped
test values use the never-issued 000 / 9xx area numbers).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.exc import DBAPIError

from tests.models.conftest import Seed, _insert, set_ctx, table

pytestmark = pytest.mark.services

SPLIT_SUM = "do not sum to its amount"
SPLIT_SIGN = "must have the sign of its amount"


@contextmanager
def as_owner(engine: Engine, seed: Seed) -> Iterator[Connection]:
    """A committing transaction as Alex, who owns the seeded account."""
    with engine.begin() as c:
        set_ctx(c, seed.household_id, seed.person_ids[0])
        yield c


@contextmanager
def rolled_back(engine: Engine, seed: Seed) -> Iterator[Connection]:
    with engine.connect() as c:
        tx = c.begin()
        try:
            set_ctx(c, seed.household_id, seed.person_ids[0])
            yield c
        finally:
            tx.rollback()


def _new_txn(c: Connection, seed: Seed, amount: str) -> Any:
    return _insert(
        c,
        "transaction",
        household_id=seed.household_id,
        account_id=seed.rows["account"]["id"],
        posted_date=date(2026, 2, 3),
        amount=Decimal(amount),
        description_raw="EXAMPLE SPLIT",
        source="manual",
    )


def _split(c: Connection, seed: Seed, txn: Any, amount: str) -> Any:
    return _insert(
        c,
        "transaction_split",
        household_id=seed.household_id,
        transaction_id=txn,
        amount=Decimal(amount),
    )


def _drop_txn(engine: Engine, seed: Seed, txn: Any) -> None:
    t = table("transaction")
    with as_owner(engine, seed) as c:
        c.execute(t.delete().where(t.c.id == txn))


# --------------------------------------------------------------------------- splits


def test_exact_splits_commit(app_engine: Engine, two_households: tuple[Seed, Seed]) -> None:
    a, _ = two_households
    with as_owner(app_engine, a) as c:
        txn = _new_txn(c, a, "-100.00")
        _split(c, a, txn, "-60.01")
        _split(c, a, txn, "-39.99")
    with as_owner(app_engine, a) as c:
        s = table("transaction_split")
        amounts = c.execute(select(s.c.amount).where(s.c.transaction_id == txn)).scalars().all()
        assert sorted(amounts) == [Decimal("-60.01"), Decimal("-39.99")]
    _drop_txn(app_engine, a, txn)


def test_mismatched_splits_are_rejected_at_commit(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    with pytest.raises(DBAPIError, match=SPLIT_SUM), as_owner(app_engine, a) as c:
        txn = _new_txn(c, a, "-100.00")
        _split(c, a, txn, "-60.00")
        _split(c, a, txn, "-39.99")  # one cent short
        # Deferred: nothing fails until COMMIT.
        assert c.execute(text("SELECT 1")).scalar_one() == 1


def test_splits_can_be_rewritten_within_a_transaction(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    with as_owner(app_engine, a) as c:
        txn = _new_txn(c, a, "-10.00")
        first = _split(c, a, txn, "-10.00")
    # Re-split: delete the old part and add two new ones in one unit of work.
    s = table("transaction_split")
    with as_owner(app_engine, a) as c:
        c.execute(s.delete().where(s.c.id == first))
        _split(c, a, txn, "-4.00")
        _split(c, a, txn, "-6.00")
    _drop_txn(app_engine, a, txn)


@pytest.mark.parametrize(
    ("parent", "parts"),
    [
        ("-10.00", ("-15.00", "5.00")),  # sums right, opposite-sign part
        ("-10.00", ("-10.00", "0.00")),  # zero part
        ("25.00", ("30.00", "-5.00")),  # inflow with a negative part
    ],
)
def test_split_signs_must_match_the_parent(
    app_engine: Engine, two_households: tuple[Seed, Seed], parent: str, parts: tuple[str, ...]
) -> None:
    a, _ = two_households
    with pytest.raises(DBAPIError, match=SPLIT_SIGN), as_owner(app_engine, a) as c:
        txn = _new_txn(c, a, parent)
        for p in parts:
            _split(c, a, txn, p)


def test_changing_the_parent_amount_rechecks_splits(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    t = table("transaction")
    s = table("transaction_split")
    with as_owner(app_engine, a) as c:
        txn = _new_txn(c, a, "-20.00")
        _split(c, a, txn, "-20.00")
    with pytest.raises(DBAPIError, match=SPLIT_SUM), as_owner(app_engine, a) as c:
        c.execute(t.update().where(t.c.id == txn).values(amount=Decimal("-21.00")))
    # Amount and splits together: fine.
    with as_owner(app_engine, a) as c:
        c.execute(t.update().where(t.c.id == txn).values(amount=Decimal("-21.00")))
        c.execute(s.update().where(s.c.transaction_id == txn).values(amount=Decimal("-21.00")))
    _drop_txn(app_engine, a, txn)


def test_deleting_one_of_two_splits_is_rejected_but_all_is_fine(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    s = table("transaction_split")
    with as_owner(app_engine, a) as c:
        txn = _new_txn(c, a, "-8.00")
        one = _split(c, a, txn, "-3.00")
        _split(c, a, txn, "-5.00")
    with pytest.raises(DBAPIError, match=SPLIT_SUM), as_owner(app_engine, a) as c:
        c.execute(s.delete().where(s.c.id == one))
    with as_owner(app_engine, a) as c:
        c.execute(s.delete().where(s.c.transaction_id == txn))
    _drop_txn(app_engine, a, txn)


def test_moving_a_split_to_another_transaction_checks_both(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    s = table("transaction_split")
    with as_owner(app_engine, a) as c:
        t1 = _new_txn(c, a, "-5.00")
        t2 = _new_txn(c, a, "-5.00")
        moving = _split(c, a, t1, "-5.00")
        _split(c, a, t2, "-5.00")
    with pytest.raises(DBAPIError, match=SPLIT_SUM), as_owner(app_engine, a) as c:
        c.execute(s.update().where(s.c.id == moving).values(transaction_id=t2))
    _drop_txn(app_engine, a, t1)
    _drop_txn(app_engine, a, t2)


def test_split_check_restores_household_wide(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    """The check widens its own view to count every split, then leaves the caller's
    household_wide setting as it found it."""
    a, _ = two_households
    with as_owner(app_engine, a) as c:
        txn = _new_txn(c, a, "-7.00")
        _split(c, a, txn, "-7.00")
        c.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert c.execute(text("SELECT app_household_wide()")).scalar_one() is False
    _drop_txn(app_engine, a, txn)


def test_split_balance_trigger_is_deferred(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT tgrelid::regclass::text, tgdeferrable, tginitdeferred FROM pg_trigger"
                " WHERE tgname = 'transaction_split_balance' ORDER BY 1"
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("transaction", True, True),
        ("transaction_split", True, True),
    ]


# --------------------------------------------------------------------------- transfers


def test_transfer_group_requires_is_transfer(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    t = table("transaction")
    base = {
        "household_id": a.household_id,
        "account_id": a.rows["account"]["id"],
        "posted_date": date(2026, 2, 4),
        "amount": Decimal("-50.00"),
        "description_raw": "EXAMPLE TRANSFER",
        "source": "manual",
        "transfer_group_id": uuid.uuid4(),
    }
    with rolled_back(app_engine, a) as c, pytest.raises(DBAPIError, match="transfer_group"):
        c.execute(t.insert().values(**base))
    with rolled_back(app_engine, a) as c:
        c.execute(t.insert().values(**base, is_transfer=True))


def test_pending_external_id_is_indexed(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        defs = c.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
            {"n": "ix_transaction_pending_external_id"},
        ).scalar_one()
    assert "(pending_external_id)" in defs


# --------------------------------------------------------------------------- CHECKs

# (table, column, bad value): each must be rejected by a CHECK.
BAD_VALUES: list[tuple[str, str, Any]] = [
    ("investment_txn", "fees", Decimal("-0.01")),
    ("investment_txn", "price", Decimal("-1.00000000")),
    ("liability", "apr", Decimal("-1.0000")),
    ("liability", "min_payment", Decimal("-25.00")),
    ("liability", "original_principal", Decimal("-1.00")),
    ("balance_snapshot", "limit_amt", Decimal("-500.00")),
    ("budget_line", "amount", Decimal("-1.00")),
    ("budget_line", "alert_pct", Decimal("1.5000")),
    ("budget_line", "alert_pct", Decimal("-0.1000")),
    ("tradeline", "credit_limit", Decimal("-1.00")),
    ("tradeline", "balance", Decimal("-1.00")),
    ("tradeline", "high_balance", Decimal("-1.00")),
    ("parsed_field", "confidence", Decimal("1.0001")),
    ("tax_item", "confidence", Decimal("-0.0001")),
    ("estimate_snapshot", "federal_liability", Decimal("-1.00")),
    ("estimate_snapshot", "state_liability", Decimal("-1.00")),
    ("estimate_snapshot", "withheld", Decimal("-1.00")),
    ("agent_finding", "est_savings", Decimal("-1.00")),
    ("account", "mask", "4000-0000-0000-0002"),
    ("account", "mask", "00000"),
    ("account", "mask", "0 0 0 0 0"),
    ("tradeline", "account_number_masked", "XXXX-XXXX-0000-0000"),
    ("parsed_field", "value", "000-12-3456"),
    ("parsed_field", "value", "id 900123456 on file"),
    ("parsed_field", "value", "000 12 3456"),
    ("household", "name", ""),
    ("household", "name", "x" * 201),
    ("person", "display_name", ""),
    ("person", "display_name", "x" * 201),
]

# (table, column, good value): each must be accepted.
GOOD_VALUES: list[tuple[str, str, Any]] = [
    ("estimate_snapshot", "projected_due", Decimal("-300.00")),  # a refund
    ("liability", "statement_balance", Decimal("-5.00")),  # a credit balance
    ("budget_line", "alert_pct", Decimal("1.0000")),
    ("budget_line", "alert_pct", Decimal("0")),
    ("account", "mask", "XXXX-0000"),
    ("account", "mask", "0000"),
    ("tradeline", "account_number_masked", "****0000"),
    ("parsed_field", "value", "XXX-XX-0000"),
    ("parsed_field", "value", "00000-0000"),  # ZIP+4
    ("parsed_field", "value", "5550000000"),  # a 10-digit phone number
    ("parsed_field", "value", "1234567.89"),
    ("person", "display_name", "x" * 200),
]


def _update_seeded(c: Connection, seed: Seed, name: str, column: str, value: Any) -> int:
    t = table(name)
    ident = {"id": seed.household_id} if name == "household" else seed.rows[name]
    stmt = t.update().values({column: value})
    for k, v in ident.items():
        stmt = stmt.where(t.c[k] == v)
    return int(c.execute(stmt).rowcount)


@pytest.mark.parametrize(("name", "column", "value"), BAD_VALUES)
def test_check_rejects(
    app_engine: Engine, two_households: tuple[Seed, Seed], name: str, column: str, value: Any
) -> None:
    a, _ = two_households
    with rolled_back(app_engine, a) as c, pytest.raises(DBAPIError, match="check constraint"):
        _update_seeded(c, a, name, column, value)


@pytest.mark.parametrize(("name", "column", "value"), GOOD_VALUES)
def test_check_accepts(
    app_engine: Engine, two_households: tuple[Seed, Seed], name: str, column: str, value: Any
) -> None:
    a, _ = two_households
    with rolled_back(app_engine, a) as c:
        assert _update_seeded(c, a, name, column, value) == 1


@pytest.mark.parametrize(
    ("dependents", "ok"),
    [
        ([{"name": "Example Child", "birth_year": 2018}], True),
        ([{"name": "Example Child", "ssn_last4": "XXX-XX-0000"}], True),
        ([{"name": "Example Child", "ssn": "000-12-3456"}], False),
        ([{"name": "Example Child", "id": "900123456"}], False),
    ],
)
def test_dependents_reject_ssns(
    app_engine: Engine, two_households: tuple[Seed, Seed], dependents: Any, ok: bool
) -> None:
    a, _ = two_households
    with rolled_back(app_engine, a) as c:
        if ok:
            assert _update_seeded(c, a, "tax_year", "dependents", dependents) == 1
        else:
            with pytest.raises(DBAPIError, match="dependents_no_ssn"):
                _update_seeded(c, a, "tax_year", "dependents", dependents)


def test_create_household_enforces_name_length(app_engine: Engine) -> None:
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="name_length"):
        c.execute(text("SELECT create_household(:n)"), {"n": "x" * 201})
