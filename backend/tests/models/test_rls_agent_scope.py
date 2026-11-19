"""Agent output inherits the run's visibility (ADR 0008).

A run for Alex may have read Alex's private accounts, so its conversation, messages and findings
are visible only to Alex (or household-wide jobs). Household-level runs (no person) are visible
to everyone in the household.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError

from tests.models.conftest import _insert, delete_household, set_ctx, table

pytestmark = pytest.mark.services


@dataclass
class AgentHousehold:
    household_id: uuid.UUID
    alex: uuid.UUID
    sam: uuid.UUID
    # scope ('alex' | 'household') -> row id, per table
    ids: dict[str, dict[str, uuid.UUID]]


@pytest.fixture(scope="module")
def household(app_engine: Engine, owner_engine: Engine) -> Iterator[AgentHousehold]:
    with app_engine.begin() as c:
        hid = c.execute(text("SELECT create_household('Example Agent')")).scalar_one()
        set_ctx(c, hid, wide=True)
        h = {"household_id": hid}
        alex = _insert(c, "person", **h, display_name="Alex Example", filing_role="primary")
        sam = _insert(c, "person", **h, display_name="Sam Example", filing_role="spouse")
        ids: dict[str, dict[str, uuid.UUID]] = {
            "agent_conversation": {},
            "agent_message": {},
            "agent_finding": {},
        }
        for scope, person in (("alex", alex), ("household", None)):
            convo = _insert(c, "agent_conversation", **h, person_id=person, title=scope)
            ids["agent_conversation"][scope] = convo
            ids["agent_message"][scope] = _insert(
                c, "agent_message", **h, conversation_id=convo, role="assistant", content={}
            )
            ids["agent_finding"][scope] = _insert(
                c,
                "agent_finding",
                **h,
                person_id=person,
                category="cashflow",
                title=scope,
                body_md="Synthetic.",
            )
    yield AgentHousehold(hid, alex, sam, ids)
    delete_household(owner_engine, hid)


def _visible(engine: Engine, hh: AgentHousehold, person: uuid.UUID | None, wide: bool = False):
    out: dict[str, set[str]] = {}
    with engine.begin() as c:
        set_ctx(c, hh.household_id, person, wide=wide)
        for name, by_scope in hh.ids.items():
            got = set(c.execute(select(table(name).c.id)).scalars())
            out[name] = {scope for scope, i in by_scope.items() if i in got}
    return out


def test_person_output_is_private_to_that_person(
    app_engine: Engine, household: AgentHousehold
) -> None:
    for name, scopes in _visible(app_engine, household, household.sam).items():
        assert scopes == {"household"}, name
    for name, scopes in _visible(app_engine, household, household.alex).items():
        assert scopes == {"alex", "household"}, name


def test_no_person_sees_only_household_output(
    app_engine: Engine, household: AgentHousehold
) -> None:
    for name, scopes in _visible(app_engine, household, None).items():
        assert scopes == {"household"}, name


def test_household_wide_sees_all_output(app_engine: Engine, household: AgentHousehold) -> None:
    for name, scopes in _visible(app_engine, household, None, wide=True).items():
        assert scopes == {"alex", "household"}, name


def test_cannot_write_output_for_another_person(
    app_engine: Engine, household: AgentHousehold
) -> None:
    h = {"household_id": household.household_id}
    for name, values in (
        (
            "agent_finding",
            {**h, "person_id": household.alex, "category": "x", "title": "x", "body_md": "x"},
        ),
        ("agent_conversation", {**h, "person_id": household.alex}),
        (
            "agent_message",
            {
                **h,
                "conversation_id": household.ids["agent_conversation"]["alex"],
                "role": "user",
                "content": {},
            },
        ),
    ):
        with app_engine.connect() as c, c.begin():
            set_ctx(c, household.household_id, household.sam)
            with pytest.raises(DBAPIError, match="row-level security"):
                c.execute(table(name).insert().values(**values))


def test_other_person_cannot_publish_private_output(
    app_engine: Engine, household: AgentHousehold
) -> None:
    """Sam can't see Alex's finding, so can't clear its person_id to make it household-wide."""
    f = table("agent_finding")
    with app_engine.connect() as c, c.begin():
        set_ctx(c, household.household_id, household.sam)
        upd = c.execute(
            f.update()
            .where(f.c.id == household.ids["agent_finding"]["alex"])
            .values(person_id=None)
        )
        assert upd.rowcount == 0
