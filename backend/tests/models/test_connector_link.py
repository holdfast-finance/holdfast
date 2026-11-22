"""P4a schema (migration 0011): slot ledger, link decisions, coverage, adoption guard.

Every data assertion runs as ``holdfast_app`` under RLS, except where the owner role is the
point (the DELETE refusal binds it too). All data is synthetic.

Acceptance (contract §6.1, P4a): RLS lint for the new tables; no DELETE on ``plaid_link_slot``;
two households on one client get one ``reserved`` row; the trigger matrix, including a sandbox
connection refused; a person can't read or insert coverage on another person's private or shared
account.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.models.conftest import CONNECTOR_TABLES, delete_household, set_ctx, table

pytestmark = pytest.mark.services

NEW_TABLES = CONNECTOR_TABLES
FP_A = "0123456789abcdef"
FP_B = "fedcba9876543210"
WHEN = datetime(2026, 11, 1, tzinfo=UTC)
RLS_VIOLATION = "row-level security"


def _insert(c: Connection, table_name: str, /, **values: Any) -> Any:
    t = table(table_name)
    stmt = t.insert().values(**values)
    if "id" in t.c:
        return c.execute(stmt.returning(t.c.id)).scalar_one()
    c.execute(stmt)
    return None


@dataclass
class World:
    hid: uuid.UUID
    alex: uuid.UUID
    sam: uuid.UUID
    prod: uuid.UUID  # production Plaid connection
    sandbox: uuid.UUID  # sandbox Plaid connection
    private: uuid.UUID  # Alex's private imported account
    shared: uuid.UUID  # Alex's shared imported account
    summary: uuid.UUID  # Alex's summary_only imported account
    joint: uuid.UUID  # joint imported account
    run: uuid.UUID  # a reconciled-shaped import run on the private account


def _account(c: Connection, hid: uuid.UUID, owner: uuid.UUID | None, visibility: str) -> Any:
    key = f"example_bank.checking:0000:{uuid.uuid4().hex[:12]}"
    return _insert(
        c,
        "account",
        household_id=hid,
        owner_person_id=owner,
        visibility=visibility,
        source="import",
        external_account_id=key,
        statement_key=key,
        name="Example Checking",
        mask="0000",
        type="depository",
    )


def _world(app_engine: Engine, label: str) -> World:
    with app_engine.begin() as c:
        hid = c.execute(
            text("SELECT create_household(:n)"), {"n": f"Example Connector Household {label}"}
        ).scalar_one()
        set_ctx(c, hid, wide=True)
        h = {"household_id": hid}
        alex = _insert(c, "person", **h, display_name=f"Alex {label}", filing_role="primary")
        sam = _insert(c, "person", **h, display_name=f"Sam {label}", filing_role="spouse")
        conns = [
            _insert(
                c,
                "bank_connection",
                **h,
                provider="plaid",
                external_id=f"item-{uuid.uuid4()}",
                access_token_enc=b"ciphertext",
                env=env,
                status="pending_setup",
                client_fp=FP_A,
            )
            for env in ("production", "sandbox")
        ]
        private = _account(c, hid, alex, "private")
        shared = _account(c, hid, alex, "shared")
        summary = _account(c, hid, alex, "summary_only")
        joint = _account(c, hid, None, "shared")
        doc = _insert(
            c,
            "document",
            **h,
            person_id=alex,
            kind="statement",
            filename="statement.pdf",
            sha256=uuid.uuid4().hex * 2,
            storage_path=f"statements/{uuid.uuid4()}.pdf",
            mime="application/pdf",
            size_bytes=10,
        )
        run = _insert(c, "import_run", **h, document_id=doc, account_id=private)
    return World(hid, alex, sam, conns[0], conns[1], private, shared, summary, joint, run)


@pytest.fixture
def world(app_engine: Engine, owner_engine: Engine) -> Iterator[World]:
    w = _world(app_engine, "A")
    yield w
    delete_household(owner_engine, w.hid)


@pytest.fixture
def two_worlds(app_engine: Engine, owner_engine: Engine) -> Iterator[tuple[World, World]]:
    a, b = _world(app_engine, "A"), _world(app_engine, "B")
    yield a, b
    delete_household(owner_engine, a.hid)
    delete_household(owner_engine, b.hid)


def _slot(c: Connection, hid: uuid.UUID, **values: Any) -> Any:
    row: dict[str, Any] = {
        "household_id": hid,
        "client_fp": FP_A,
        "env": "production",
        "owner_count": 3,
        "created_by_user_id": uuid.uuid4(),
        "link_token_enc": b"sealed-link-token",
    }
    row.update(values)
    return _insert(c, "plaid_link_slot", **row)


def _coverage(c: Connection, w: World, account: uuid.UUID) -> Any:
    return _insert(
        c,
        "account_coverage",
        household_id=w.hid,
        account_id=account,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        source_import_run_id=w.run,
    )


def _seed_rows(app_engine: Engine, w: World) -> dict[str, uuid.UUID]:
    with app_engine.begin() as c:
        set_ctx(c, w.hid, wide=True)
        link = _insert(
            c,
            "connector_account_link",
            household_id=w.hid,
            bank_connection_id=w.prod,
            external_account_id=f"acct-{uuid.uuid4().hex}",
        )
        slot = _slot(c, w.hid, client_fp=uuid.uuid4().hex[:16])
        cov = _coverage(c, w, w.private)
    return {"connector_account_link": link, "plaid_link_slot": slot, "account_coverage": cov}


# --------------------------------------------------------------------------- RLS lint


def test_new_tables_have_forced_rls_and_household_isolation(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        for name in NEW_TABLES:
            enabled, forced = c.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class"
                    " WHERE relname = :t AND relnamespace = 'public'::regnamespace"
                ),
                {"t": name},
            ).one()
            assert (enabled, forced) == (True, True), name
            policies = set(
                c.execute(
                    text("SELECT policyname FROM pg_policies WHERE tablename = :t"), {"t": name}
                ).scalars()
            )
            assert "household_isolation" in policies, name
        coverage = dict(
            c.execute(
                text(
                    "SELECT policyname, permissive FROM pg_policies"
                    " WHERE tablename = 'account_coverage' AND policyname LIKE 'person_%'"
                )
            ).all()
        )
    assert coverage == {
        "person_read": "RESTRICTIVE",
        "person_insert": "RESTRICTIVE",
        "person_update": "RESTRICTIVE",
        "person_delete": "RESTRICTIVE",
    }


def test_app_grants_on_new_tables(owner_engine: Engine) -> None:
    expected = {
        "connector_account_link": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "account_coverage": {"SELECT", "INSERT", "UPDATE", "DELETE"},
        "plaid_link_slot": {"SELECT", "INSERT", "UPDATE"},
    }
    with owner_engine.connect() as c:
        for name, privileges in expected.items():
            got = {
                p
                for p in (
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                    "TRUNCATE",
                    "REFERENCES",
                    "TRIGGER",
                )
                if c.execute(
                    text("SELECT has_table_privilege('holdfast_app', :t, :p)"),
                    {"t": f"public.{name}", "p": p},
                ).scalar_one()
            }
            assert got == privileges, name


# --------------------------------------------------------------------------- household isolation


@pytest.mark.parametrize("name", NEW_TABLES)
def test_other_household_rows_are_invisible_and_untouchable(
    app_engine: Engine, two_worlds: tuple[World, World], name: str
) -> None:
    a, b = two_worlds
    ids = _seed_rows(app_engine, a)
    t = table(name)
    with app_engine.begin() as c:
        set_ctx(c, b.hid, wide=True)
        assert c.execute(t.select().where(t.c.id == ids[name])).first() is None
        assert c.execute(t.update().where(t.c.id == ids[name]).values(id=t.c.id)).rowcount == 0
        if name != "plaid_link_slot":  # no DELETE grant at all (below)
            assert c.execute(t.delete().where(t.c.id == ids[name])).rowcount == 0
    with app_engine.begin() as c:
        set_ctx(c, a.hid, wide=True)
        assert c.execute(t.select().where(t.c.id == ids[name])).first() is not None
    with app_engine.connect() as c:  # no context: nothing at all
        assert c.execute(t.select()).first() is None


def test_cannot_insert_into_another_household(
    app_engine: Engine, two_worlds: tuple[World, World]
) -> None:
    a, b = two_worlds
    attempts = (
        lambda c: _insert(
            c,
            "connector_account_link",
            household_id=a.hid,
            bank_connection_id=a.prod,
            external_account_id="acct-x",
        ),
        lambda c: _slot(c, a.hid, client_fp=FP_B),
        lambda c: _coverage(c, a, a.joint),
    )
    for attempt in attempts:
        with app_engine.begin() as c, pytest.raises(DBAPIError, match=RLS_VIOLATION):
            set_ctx(c, b.hid, wide=True)
            attempt(c)


# --------------------------------------------------------------------------- coverage, per person


def _person_coverage(app_engine: Engine, w: World) -> dict[uuid.UUID, uuid.UUID]:
    with app_engine.begin() as c:
        set_ctx(c, w.hid, wide=True)
        return {a: _coverage(c, w, a) for a in (w.private, w.shared, w.summary, w.joint)}


def test_a_person_cannot_read_another_persons_coverage(app_engine: Engine, world: World) -> None:
    cov = _person_coverage(app_engine, world)
    t = table("account_coverage")
    with app_engine.begin() as c:
        set_ctx(c, world.hid, world.sam)
        seen = set(c.execute(t.select().with_only_columns(t.c.account_id)).scalars())
    # Alex's private and summary_only accounts hide their details from Sam; the shared one shows
    # them (like transactions), and the joint one is Sam's too.
    assert seen == {world.shared, world.joint}
    with app_engine.begin() as c:
        set_ctx(c, world.hid, world.alex)
        assert set(c.execute(t.select().with_only_columns(t.c.id)).scalars()) == set(cov.values())


@pytest.mark.parametrize("which", ["private", "shared", "summary"])
def test_a_person_cannot_insert_coverage_on_another_persons_account(
    app_engine: Engine, world: World, which: str
) -> None:
    account = getattr(world, which)
    with app_engine.begin() as c, pytest.raises(DBAPIError, match=RLS_VIOLATION):
        set_ctx(c, world.hid, world.sam)
        _coverage(c, world, account)
    with app_engine.begin() as c:  # the owner can
        set_ctx(c, world.hid, world.alex)
        _coverage(c, world, account)


def test_a_person_cannot_change_or_delete_another_persons_coverage(
    app_engine: Engine, world: World
) -> None:
    cov = _person_coverage(app_engine, world)
    t = table("account_coverage")
    with app_engine.begin() as c:
        set_ctx(c, world.hid, world.sam)
        for account in (world.private, world.shared, world.summary):
            row = t.c.id == cov[account]
            assert (
                c.execute(t.update().where(row).values(end_date=date(2026, 12, 31))).rowcount == 0
            )
            assert c.execute(t.delete().where(row)).rowcount == 0
        # Joint accounts are everyone's.
        assert c.execute(t.delete().where(t.c.id == cov[world.joint])).rowcount == 1
    with app_engine.begin() as c, pytest.raises(DBAPIError, match=RLS_VIOLATION):
        set_ctx(c, world.hid, world.sam)
        # Moving joint coverage onto Alex's account needs write rights on the new account too.
        joint_cov = _coverage(c, world, world.joint)
        c.execute(t.update().where(t.c.id == joint_cov).values(account_id=world.shared))


def test_coverage_period_order(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c, pytest.raises(IntegrityError, match="period_order"):
        set_ctx(c, world.hid, wide=True)
        _insert(
            c,
            "account_coverage",
            household_id=world.hid,
            account_id=world.joint,
            start_date=date(2026, 2, 1),
            end_date=date(2026, 1, 1),
        )


# --------------------------------------------------------------------------- slot ledger


def test_app_cannot_delete_slot_rows(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        slot = _slot(c, world.hid)
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="permission denied"):
        set_ctx(c, world.hid, wide=True)
        c.execute(text("DELETE FROM plaid_link_slot WHERE id = :i"), {"i": slot})


def test_owner_cannot_delete_slot_rows_but_a_household_cascade_can(
    app_engine: Engine, owner_engine: Engine
) -> None:
    w = _world(app_engine, "C")
    with app_engine.begin() as c:
        set_ctx(c, w.hid, wide=True)
        slot = _slot(c, w.hid, client_fp=FP_B)
    try:
        with owner_engine.begin() as c, pytest.raises(DBAPIError, match="never deleted"):
            set_ctx(c, w.hid, wide=True)
            c.execute(text("DELETE FROM plaid_link_slot WHERE id = :i"), {"i": slot})
    finally:
        delete_household(owner_engine, w.hid)  # the cascade is allowed
    with owner_engine.connect() as c:
        left = c.execute(
            text("SELECT count(*) FROM plaid_link_slot WHERE id = :i"), {"i": slot}
        ).scalar_one()
    assert left == 0


def test_two_households_on_one_client_get_one_reserved_row(
    app_engine: Engine, two_worlds: tuple[World, World]
) -> None:
    a, b = two_worlds
    with app_engine.begin() as c:
        set_ctx(c, a.hid, wide=True)
        first = _slot(c, a.hid)
    with app_engine.begin() as c, pytest.raises(IntegrityError, match="client_fp_env_reserved"):
        set_ctx(c, b.hid, wide=True)
        _slot(c, b.hid)
    with app_engine.begin() as c:  # a sandbox reservation on the same client is separate
        set_ctx(c, b.hid, wide=True)
        _slot(c, b.hid, env="sandbox", owner_count=None)
    with app_engine.begin() as c:  # once resolved, the client can reserve again
        set_ctx(c, a.hid, wide=True)
        c.execute(
            text(
                "UPDATE plaid_link_slot SET state = 'linked', item_id = 'item-a',"
                " link_token_enc = NULL, resolved_at = now() WHERE id = :i"
            ),
            {"i": first},
        )
    with app_engine.begin() as c:
        set_ctx(c, b.hid, wide=True)
        _slot(c, b.hid)


def test_link_token_only_while_reserved(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        slot = _slot(c, world.hid)
    with (
        app_engine.begin() as c,
        pytest.raises(IntegrityError, match="link_token_only_while_reserved"),
    ):
        set_ctx(c, world.hid, wide=True)
        c.execute(
            text("UPDATE plaid_link_slot SET state = 'unknown', resolved_at = now() WHERE id = :i"),
            {"i": slot},
        )
    with (
        app_engine.begin() as c,
        pytest.raises(IntegrityError, match="link_token_only_while_reserved"),
    ):
        set_ctx(c, world.hid, wide=True)
        _slot(c, world.hid, state="abandoned", resolved_at=WHEN, client_fp=FP_B)


def _set_state(c: Connection, slot: uuid.UUID, state: str) -> None:
    c.execute(
        text(
            "UPDATE plaid_link_slot SET state = CAST(:s AS text), link_token_enc = NULL,"
            " resolved_at = coalesce(resolved_at, now()),"
            " item_id = CASE WHEN CAST(:s AS text) = 'linked'"
            " THEN coalesce(item_id, 'item-' || id) ELSE item_id END"
            " WHERE id = :i"
        ),
        {"s": state, "i": slot},
    )


@pytest.mark.parametrize(
    ("start", "end", "allowed"),
    [
        ("reserved", "linked", True),
        ("reserved", "abandoned", True),
        ("reserved", "unknown", True),
        ("unknown", "abandoned", True),
        ("unknown", "linked", False),
        ("unknown", "reserved", False),
        ("linked", "abandoned", False),
        ("linked", "unknown", False),
        ("abandoned", "reserved", False),
        ("abandoned", "linked", False),
    ],
)
def test_slot_transitions(
    app_engine: Engine, world: World, start: str, end: str, allowed: bool
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        slot = _slot(c, world.hid)
        if start != "reserved":
            _set_state(c, slot, start)
    if allowed:
        with app_engine.begin() as c:
            set_ctx(c, world.hid, wide=True)
            _set_state(c, slot, end)
        return
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="cannot become"):
        set_ctx(c, world.hid, wide=True)
        _set_state(c, slot, end)


@pytest.mark.parametrize(
    "change",
    [
        "client_fp = 'fedcba9876543210'",
        "env = 'sandbox'",
        "owner_count = 9",
        "created_by_user_id = gen_random_uuid()",
        "created_at = now() - interval '1 day'",
        "item_id = 'item-other'",
    ],
)
def test_slot_facts_are_final(app_engine: Engine, world: World, change: str) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        slot = _slot(c, world.hid)
        _set_state(c, slot, "linked")
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="is final"):
        set_ctx(c, world.hid, wide=True)
        c.execute(text(f"UPDATE plaid_link_slot SET {change} WHERE id = :i"), {"i": slot})  # noqa: S608


def test_slot_constraints(app_engine: Engine, world: World) -> None:
    bad: list[tuple[dict[str, Any], str]] = [
        ({"owner_count": None}, "production_has_owner_count"),
        ({"owner_count": 11}, "owner_count_range"),
        ({"client_fp": "NOT-A-FINGERPRINT"}, "client_fp_format"),
        ({"flag": "free text with spaces"}, "flag_is_code"),
        ({"state": "linked", "link_token_enc": None, "resolved_at": WHEN}, "linked_has_item"),
        ({"resolved_at": WHEN}, "resolved_when_done"),
    ]
    for values, name in bad:
        with app_engine.begin() as c, pytest.raises(IntegrityError, match=name):
            set_ctx(c, world.hid, wide=True)
            _slot(c, world.hid, **values)


def test_plaid_slot_count_spans_households_and_skips_abandoned(
    app_engine: Engine, two_worlds: tuple[World, World]
) -> None:
    a, b = two_worlds
    fp = uuid.uuid4().hex[:16]
    with app_engine.begin() as c:
        set_ctx(c, a.hid, wide=True)
        for state in ("linked", "abandoned", "unknown"):
            _set_state(c, _slot(c, a.hid, client_fp=fp), state)
        _slot(c, a.hid, client_fp=fp, env="sandbox", owner_count=None)
    with app_engine.begin() as c:
        set_ctx(c, b.hid, wide=True)
        _slot(c, b.hid, client_fp=fp)  # reserved counts
        # B can't read A's rows, but the count is per client: 3 = linked + unknown + reserved.
        rows = c.execute(
            text("SELECT count(*) FROM plaid_link_slot WHERE client_fp = :f"), {"f": fp}
        ).scalar_one()
        assert rows == 1
        n = c.execute(text("SELECT plaid_slot_count(:f, 'production')"), {"f": fp}).scalar_one()
        assert n == 3
        assert (
            c.execute(text("SELECT plaid_slot_count(:f, 'sandbox')"), {"f": fp}).scalar_one() == 1
        )
    with app_engine.connect() as c:  # no context needed, and it returns a number only
        n = c.execute(text("SELECT plaid_slot_count(:f, 'production')"), {"f": fp}).scalar_one()
        assert n == 3


def test_plaid_slot_count_is_a_pinned_security_definer(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        definer, config, app_exec, public_exec = c.execute(
            text(
                "SELECT p.prosecdef, p.proconfig,"
                " has_function_privilege('holdfast_app', p.oid, 'EXECUTE'),"
                " has_function_privilege('public', p.oid, 'EXECUTE')"
                " FROM pg_proc p WHERE p.proname = 'plaid_slot_count'"
            )
        ).one()
    assert definer is True
    assert config == ["search_path=pg_catalog, pg_temp"]
    assert (app_exec, public_exec) == (True, False)


def test_deleting_a_connection_keeps_its_slot(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        slot = _slot(c, world.hid, bank_connection_id=world.prod)
        _set_state(c, slot, "linked")
        c.execute(text("DELETE FROM bank_connection WHERE id = :i"), {"i": world.prod})
        row = c.execute(
            text("SELECT state, bank_connection_id FROM plaid_link_slot WHERE id = :i"),
            {"i": slot},
        ).one()
    assert tuple(row) == ("linked", None)


# --------------------------------------------------------------------------- connector links


def test_connector_link_constraints_and_cascades(app_engine: Engine, world: World) -> None:
    t = table("connector_account_link")
    base = {"household_id": world.hid, "bank_connection_id": world.prod}
    bad: list[tuple[dict[str, Any], str]] = [
        ({"state": "mapped", "decided_at": WHEN}, "mapped_has_account"),
        ({"account_id": world.joint}, "mapped_has_account"),
        ({"state": "ignored"}, "decided_when_done"),
        ({"status_code": "Login failed for this user"}, "status_code_is_code"),
    ]
    for values, name in bad:
        with app_engine.begin() as c, pytest.raises(IntegrityError, match=name):
            set_ctx(c, world.hid, wide=True)
            _insert(c, "connector_account_link", **base, external_account_id="acct-1", **values)
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        _insert(c, "connector_account_link", **base, external_account_id="acct-1")
        mapped = _insert(
            c,
            "connector_account_link",
            **base,
            external_account_id="acct-2",
            state="mapped",
            account_id=world.joint,
            decided_at=WHEN,
            status_code="con.auth",
        )
    with app_engine.begin() as c, pytest.raises(IntegrityError, match="uq_connector_account_link"):
        set_ctx(c, world.hid, wide=True)
        _insert(c, "connector_account_link", **base, external_account_id="acct-1")
    # A mapped account can't be deleted under its link.
    with app_engine.begin() as c, pytest.raises(IntegrityError, match="fk_connector_account_link"):
        set_ctx(c, world.hid, wide=True)
        c.execute(text("DELETE FROM account WHERE id = :i"), {"i": world.joint})
    # Deleting the connection deletes its links.
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        c.execute(text("DELETE FROM bank_connection WHERE id = :i"), {"i": world.prod})
        assert c.execute(t.select().where(t.c.id == mapped)).first() is None


# --------------------------------------------------------------------------- bank connection


def test_bank_connection_status_and_client_fp(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c, pytest.raises(IntegrityError, match="client_fp_format"):
        set_ctx(c, world.hid, wide=True)
        c.execute(
            text("UPDATE bank_connection SET client_fp = 'PLAID-CLIENT-ID' WHERE id = :i"),
            {"i": world.prod},
        )
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        status = c.execute(
            text("SELECT status FROM bank_connection WHERE id = :i"), {"i": world.prod}
        ).scalar_one()
    assert status == "pending_setup"


def test_connector_snapshots_survive_their_connection(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        _insert(
            c,
            "balance_snapshot",
            household_id=world.hid,
            account_id=world.joint,
            as_of_date=date(2026, 2, 1),
            current=Decimal("10.00"),
            bank_connection_id=world.prod,
        )
        c.execute(text("DELETE FROM bank_connection WHERE id = :i"), {"i": world.prod})
        left = c.execute(
            text("SELECT bank_connection_id FROM balance_snapshot WHERE account_id = :a"),
            {"a": world.joint},
        ).one()
    assert tuple(left) == (None,)


# --------------------------------------------------------------------------- adoption guard


def _update_account(
    conn: Connection, account: uuid.UUID, assignments: str, /, **params: Any
) -> None:
    sql = text(f"UPDATE account SET {assignments} WHERE id = :a")  # noqa: S608
    conn.execute(sql, {"a": account, **params})


ADOPT = (
    "source = :src, bank_connection_id = :c, external_account_id = 'plaid-acct', adopted_at = now()"
)


def test_adoption_by_a_production_connection(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, world.alex)
        _update_account(c, world.private, ADOPT, src="plaid", c=world.prod)
        row = c.execute(
            text("SELECT source, statement_key IS NOT NULL FROM account WHERE id = :a"),
            {"a": world.private},
        ).one()
    assert tuple(row) == ("plaid", True)


@pytest.mark.parametrize(
    ("assignments", "params", "message"),
    [
        (ADOPT, {"src": "plaid", "c": "sandbox"}, "production plaid connection"),
        (ADOPT, {"src": "simplefin", "c": "prod"}, "production simplefin connection"),
        ("source = 'plaid', bank_connection_id = :c", {"c": "prod"}, "needs its connection"),
        ("source = 'plaid', adopted_at = now()", {}, "needs its connection"),
        ("source = 'manual'", {}, "cannot change from import to manual"),
        ("statement_key = 'another-key'", {}, "statement key"),
        ("adopted_at = now()", {}, "adopted_at changes only with adoption"),
    ],
)
def test_adoption_guard_refuses(
    app_engine: Engine, world: World, assignments: str, params: dict[str, str], message: str
) -> None:
    bound = {k: getattr(world, v) if k == "c" else v for k, v in params.items()}
    with app_engine.begin() as c, pytest.raises(DBAPIError, match=message):
        set_ctx(c, world.hid, wide=True)
        _update_account(c, world.private, assignments, **bound)


def test_manual_and_native_accounts_cannot_change_source(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        manual = _insert(
            c,
            "account",
            household_id=world.hid,
            source="manual",
            name="Example Cash",
            type="depository",
        )
        native = _insert(
            c,
            "account",
            household_id=world.hid,
            source="plaid",
            bank_connection_id=world.prod,
            external_account_id="plaid-native",
            name="Example Savings",
            type="depository",
        )
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="cannot change"):
        set_ctx(c, world.hid, wide=True)
        _update_account(c, manual, "source = 'plaid', bank_connection_id = :c", c=world.prod)
    # Un-adopt needs an adopted account: a native connector account never becomes "import".
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="un-adopt"):
        set_ctx(c, world.hid, wide=True)
        _update_account(
            c,
            native,
            "source = 'import', bank_connection_id = NULL, statement_key = 'k',"
            " external_account_id = 'k'",
        )


UNADOPT = (
    "source = 'import', bank_connection_id = NULL, adopted_at = NULL,"
    " external_account_id = statement_key"
)


def test_unadopt_needs_no_connector_rows_and_a_clean_reset(
    app_engine: Engine, world: World
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        _update_account(c, world.private, ADOPT, src="plaid", c=world.prod)
        txn = _insert(
            c,
            "transaction",
            household_id=world.hid,
            account_id=world.private,
            posted_date=date(2026, 3, 1),
            amount=Decimal("-5.00"),
            description_raw="EXAMPLE COFFEE",
            source="plaid",
        )
    with app_engine.begin() as c, pytest.raises(DBAPIError, match="connector rows"):
        set_ctx(c, world.hid, wide=True)
        _update_account(c, world.private, UNADOPT)
    for half in (
        "source = 'import', adopted_at = NULL, external_account_id = statement_key",
        "source = 'import', bank_connection_id = NULL, external_account_id = statement_key",
        "source = 'import', bank_connection_id = NULL, adopted_at = NULL",
    ):
        with app_engine.begin() as c, pytest.raises(DBAPIError, match="un-adopt"):
            set_ctx(c, world.hid, wide=True)
            c.execute(text('DELETE FROM "transaction" WHERE id = :i'), {"i": txn})
            _update_account(c, world.private, half)
    with app_engine.begin() as c:
        set_ctx(c, world.hid, world.alex)
        c.execute(text('DELETE FROM "transaction" WHERE id = :i'), {"i": txn})
        _update_account(c, world.private, UNADOPT)
        row = c.execute(
            text(
                "SELECT source, bank_connection_id, adopted_at, external_account_id = statement_key"
                " FROM account WHERE id = :a"
            ),
            {"a": world.private},
        ).one()
    assert tuple(row) == ("import", None, None, True)


def test_a_person_cannot_adopt_another_persons_shared_account(
    app_engine: Engine, world: World
) -> None:
    """The account write policy decides adoption, so a shared account stays its owner's."""
    with app_engine.begin() as c:
        set_ctx(c, world.hid, world.sam)
        result = c.execute(
            text(f"UPDATE account SET {ADOPT} WHERE id = :a"),  # noqa: S608
            {"a": world.shared, "src": "plaid", "c": world.prod},
        )
        assert result.rowcount == 0


def test_statement_keys_are_unique_per_household(app_engine: Engine, world: World) -> None:
    with app_engine.begin() as c:
        set_ctx(c, world.hid, wide=True)
        key = c.execute(
            text("SELECT statement_key FROM account WHERE id = :a"), {"a": world.joint}
        ).scalar_one()
    with app_engine.begin() as c, pytest.raises(IntegrityError, match="statement_key"):
        set_ctx(c, world.hid, wide=True)
        _insert(
            c,
            "account",
            household_id=world.hid,
            source="import",
            statement_key=key,
            external_account_id=key,
            name="Twin",
            type="depository",
        )
