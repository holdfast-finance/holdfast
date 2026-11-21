"""Household isolation, enforced by Postgres RLS for the runtime role (holdfast_app).

Parametrized over every household-scoped table: household A can't see, insert, update or
delete household B's rows, and a session with no context sees nothing at all.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError

from tests.models.conftest import HOUSEHOLD_TABLES, Seed, count, set_ctx, table, where

pytestmark = pytest.mark.services

RLS_VIOLATION = "row-level security"


def _row_as_owner_household(engine: Engine, seed: Seed, name: str) -> dict[str, Any]:
    t = table(name)
    with engine.begin() as c:
        set_ctx(c, seed.household_id, wide=True)
        row = c.execute(select(t).where(where(t, seed.rows[name]))).mappings().one()
    return dict(row)


def _clone_for_insert(name: str, row: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``row`` with a fresh primary key, still pointing at the same household.

    Generated columns (``import_run.balance_diff``) are not writable.
    """
    t = table(name)
    clone = {key: value for key, value in row.items() if t.c[key].computed is None}
    if "id" in clone:
        clone["id"] = uuid.uuid4()
    elif name == "balance_snapshot":
        clone["as_of_date"] = clone["as_of_date"] + timedelta(days=1)
    elif name == "price_history":
        clone["date"] = clone["date"] + timedelta(days=1)
    elif name == "household_setting":
        clone["key"] = f"{clone['key']}.copy"
    return clone


@pytest.mark.parametrize("name", HOUSEHOLD_TABLES)
def test_other_household_rows_are_invisible(
    app_engine: Engine, two_households: tuple[Seed, Seed], name: str
) -> None:
    a, b = two_households
    with app_engine.begin() as c:
        set_ctx(c, a.household_id, a.person_ids[0])
        assert count(c, name, a.rows[name]) == 1
        assert count(c, name, b.rows[name]) == 0
        t = table(name)
        visible = c.execute(select(t.c.household_id).distinct()).scalars().all()
        assert visible == [a.household_id]


@pytest.mark.parametrize("name", HOUSEHOLD_TABLES)
def test_cannot_update_or_delete_other_household_rows(
    app_engine: Engine, two_households: tuple[Seed, Seed], name: str
) -> None:
    a, b = two_households
    t = table(name)
    with app_engine.begin() as c:
        set_ctx(c, a.household_id, a.person_ids[0])
        upd = c.execute(
            t.update().where(where(t, b.rows[name])).values(household_id=a.household_id)
        )
        assert upd.rowcount == 0
        deleted = c.execute(t.delete().where(where(t, b.rows[name])))
        assert deleted.rowcount == 0
    with app_engine.begin() as c:
        set_ctx(c, b.household_id, wide=True)
        assert count(c, name, b.rows[name]) == 1


@pytest.mark.parametrize("name", HOUSEHOLD_TABLES)
def test_cannot_insert_into_other_household(
    app_engine: Engine, two_households: tuple[Seed, Seed], name: str
) -> None:
    a, b = two_households
    clone = _clone_for_insert(name, _row_as_owner_household(app_engine, b, name))
    with app_engine.begin() as c:
        set_ctx(c, a.household_id, a.person_ids[0])
        with pytest.raises(DBAPIError, match=RLS_VIOLATION):
            c.execute(table(name).insert().values(**clone))


@pytest.mark.parametrize("name", HOUSEHOLD_TABLES)
def test_cannot_move_own_row_into_other_household(
    app_engine: Engine, two_households: tuple[Seed, Seed], name: str
) -> None:
    a, b = two_households
    t = table(name)
    with app_engine.begin() as c:
        set_ctx(c, a.household_id, a.person_ids[0])
        with pytest.raises(DBAPIError, match=RLS_VIOLATION):
            c.execute(t.update().where(where(t, a.rows[name])).values(household_id=b.household_id))


@pytest.mark.parametrize("name", (*HOUSEHOLD_TABLES, "household", "audit_log"))
def test_no_context_sees_nothing(
    app_engine: Engine, two_households: tuple[Seed, Seed], name: str
) -> None:
    with app_engine.begin() as c:
        assert count(c, name) == 0
        set_ctx(c, None, None)  # explicitly empty, as after a pooled connection is reused
        assert count(c, name) == 0


def test_household_sees_only_itself(app_engine: Engine, two_households: tuple[Seed, Seed]) -> None:
    a, b = two_households
    with app_engine.begin() as c:
        set_ctx(c, a.household_id)
        ids = c.execute(text("SELECT id FROM household")).scalars().all()
        assert ids == [a.household_id]
        assert (
            c.execute(
                text("UPDATE household SET name = 'renamed' WHERE id = :id"), {"id": b.household_id}
            ).rowcount
            == 0
        )
        with pytest.raises(DBAPIError, match="permission denied"):
            c.execute(text("DELETE FROM household WHERE id = :id"), {"id": b.household_id})


def test_app_cannot_insert_household_directly(app_engine: Engine) -> None:
    """Only create_household() makes households, even with a matching context."""
    new_id = uuid.uuid4()
    with app_engine.begin() as c:
        set_ctx(c, new_id)
        with pytest.raises(DBAPIError, match="permission denied"):
            c.execute(
                text("INSERT INTO household (id, name) VALUES (:id, 'Sneaky')"), {"id": new_id}
            )


def test_context_does_not_leak_across_transactions(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    a, _ = two_households
    with app_engine.connect() as c:
        with c.begin():
            set_ctx(c, a.household_id)
            assert count(c, "person") == 2
        with c.begin():
            assert count(c, "person") == 0


def test_composite_fk_blocks_cross_household_parent(
    app_engine: Engine, two_households: tuple[Seed, Seed]
) -> None:
    """A row can't point at another household's parent, even by a guessed id."""
    a, b = two_households
    with app_engine.begin() as c:
        set_ctx(c, a.household_id, a.person_ids[0])
        with pytest.raises(DBAPIError, match="foreign key"):
            c.execute(
                table("transaction")
                .insert()
                .values(
                    household_id=a.household_id,
                    account_id=a.rows["account"]["id"],
                    category_id=b.rows["category"]["id"],
                    posted_date=date(2026, 2, 1),
                    amount=Decimal("-1.00"),
                    description_raw="EXAMPLE",
                    source="manual",
                )
            )
