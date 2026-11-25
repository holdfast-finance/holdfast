"""Rule isolation and account helper equivalence as holdfast_app."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.exc import DBAPIError

from tests.models.conftest import _insert, delete_household, set_ctx, table

pytestmark = pytest.mark.services


@dataclass(frozen=True)
class Household:
    id: uuid.UUID
    people: tuple[uuid.UUID, uuid.UUID]
    category: uuid.UUID
    accounts: dict[str, uuid.UUID]
    transactions: dict[str, uuid.UUID]


@pytest.fixture
def households(app_engine: Engine, owner_engine: Engine) -> Iterator[tuple[Household, Household]]:
    def seed(label: str) -> Household:
        with app_engine.begin() as conn:
            hid = conn.scalar(text("SELECT create_household(:name)"), {"name": label})
            set_ctx(conn, hid, wide=True)
            alex = _insert(
                conn, "person", household_id=hid, display_name="Alex", filing_role="primary"
            )
            sam = _insert(
                conn, "person", household_id=hid, display_name="Sam", filing_role="spouse"
            )
            category = _insert(
                conn, "category", household_id=hid, slug="example", name="Example", kind="expense"
            )
            accounts = {}
            transactions = {}
            for kind in ("private", "shared", "summary_only", "joint"):
                account = _insert(
                    conn,
                    "account",
                    household_id=hid,
                    source="manual",
                    name=kind,
                    type="depository",
                    visibility="private" if kind == "joint" else kind,
                    owner_person_id=None if kind == "joint" else alex,
                )
                accounts[kind] = account
                transactions[kind] = _insert(
                    conn,
                    "transaction",
                    household_id=hid,
                    account_id=account,
                    source="manual",
                    description_raw="Example store",
                    amount=Decimal("-1.00"),
                    posted_date=date(2026, 1, 1),
                )
            return Household(hid, (alex, sam), category, accounts, transactions)

    a, b = seed("Example A"), seed("Example B")
    try:
        yield a, b
    finally:
        delete_household(owner_engine, a.id)
        delete_household(owner_engine, b.id)


def _rule(
    conn: Connection,
    hh: Household,
    person: uuid.UUID,
    scope: str = "own",
    *,
    regex: bool = False,
    enabled: bool = True,
    category: uuid.UUID | None = None,
) -> uuid.UUID:
    conditions: dict[str, object] = {"v": 1}
    if regex:
        conditions["description"] = {"mode": "regex", "value": "^EXAMPLE$"}
    return _insert(
        conn,
        "rule",
        household_id=hh.id,
        author_person_id=person,
        name="Example rule",
        category_id=category or hh.category,
        conditions=conditions,
        account_scope=scope,
        enabled=enabled,
    )


@pytest.mark.parametrize("viewer", ["alex", "sam", "wide", "other", "unset", "no_person"])
def test_rule_rls_matrix(
    app_engine: Engine, households: tuple[Household, Household], viewer: str
) -> None:
    a, b = households
    rule = table("rule")
    with app_engine.begin() as conn:
        set_ctx(conn, a.id, wide=True)
        ids = {
            (person, scope): _rule(conn, a, person, scope)
            for person in a.people
            for scope in ("own", "joint", "any")
        }
        person = dict(zip(("alex", "sam"), a.people, strict=True)).get(viewer)
        target = {"other": b.id, "unset": None}.get(viewer, a.id)
        set_ctx(conn, target, person, wide=viewer in ("wide", "other"))

        in_household = target == a.id
        visible = {
            rid
            for (author, scope), rid in ids.items()
            if in_household and (viewer == "wide" or author == person or scope != "own")
        }
        writable = {
            rid
            for (author, _), rid in ids.items()
            if in_household and (viewer == "wide" or author == person)
        }
        assert set(conn.scalars(select(rule.c.id))) == visible
        assert (
            set(conn.scalars(rule.update().values(name="Renamed").returning(rule.c.id))) == writable
        )
        assert set(conn.scalars(rule.delete().returning(rule.c.id))) == writable

        if viewer in ("alex", "sam", "wide"):
            _rule(conn, a, person or a.people[0])
        forbidden_author = a.people[1] if person == a.people[0] else a.people[0]
        if viewer != "wide":
            with pytest.raises(DBAPIError) as error, conn.begin_nested():
                _rule(conn, a, forbidden_author)
            assert error.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("viewer", ["owner", "partner", "wide", "unset"])
def test_account_helpers_agree_with_transaction_policies(
    app_engine: Engine, households: tuple[Household, Household], viewer: str
) -> None:
    a, _ = households
    with app_engine.begin() as conn:
        person = a.people[0] if viewer == "owner" else a.people[1] if viewer == "partner" else None
        set_ctx(conn, None if viewer == "unset" else a.id, person, wide=viewer == "wide")
        for kind, account in a.accounts.items():
            visible = viewer in ("owner", "wide") or (
                viewer == "partner" and kind in ("shared", "joint")
            )
            writable = viewer in ("owner", "wide") or (viewer == "partner" and kind == "joint")
            result = conn.execute(
                text("SELECT app_account_details_visible(:a), app_account_writable(:a)"),
                {"a": account},
            ).one()
            assert result == (visible, writable)
            params = {"t": a.transactions[kind]}
            assert (
                conn.scalar(text('SELECT id FROM "transaction" WHERE id = :t'), params) is not None
            ) == visible
            assert conn.execute(
                text('UPDATE "transaction" SET notes = NULL WHERE id = :t'), params
            ).rowcount == int(writable)


def test_rule_and_helper_privileges(app_engine: Engine) -> None:
    with app_engine.connect() as conn:
        assert set(
            conn.execute(
                text("""
            SELECT proname, prosecdef, proconfig, has_function_privilege(oid, 'EXECUTE'),
                   EXISTS (SELECT 1 FROM aclexplode(proacl) WHERE grantee = 0)
            FROM pg_proc WHERE pronamespace = 'public'::regnamespace
              AND proname IN ('app_account_details_visible', 'app_account_writable')
        """)
            )
        ) == {
            ("app_account_details_visible", False, None, True, False),
            ("app_account_writable", False, None, True, False),
        }
        assert set(
            conn.scalars(
                text("""
            SELECT privilege FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE',
                'TRUNCATE', 'REFERENCES', 'TRIGGER']) AS p(privilege)
            WHERE has_table_privilege('public.rule', privilege)
        """)
            )
        ) == {"SELECT", "INSERT", "UPDATE", "DELETE"}


def test_rule_guessed_foreign_keys(
    app_engine: Engine, households: tuple[Household, Household]
) -> None:
    a, b = households
    with app_engine.begin() as conn:
        set_ctx(conn, b.id, wide=True)
        other_rule = _rule(conn, b, b.people[0])
        set_ctx(conn, a.id, wide=True)
        for person, category in ((b.people[0], a.category), (a.people[0], b.category)):
            with pytest.raises(DBAPIError) as error, conn.begin_nested():
                _rule(conn, a, person, category=category)
            assert error.value.orig.sqlstate == "23503"
        with pytest.raises(DBAPIError) as error, conn.begin_nested():
            conn.execute(
                text("UPDATE \"transaction\" SET category_rule_id = :r, category_source = 'rule'"),
                {"r": other_rule},
            )
        assert error.value.orig.sqlstate == "23503"


def test_author_immutable_even_without_rls(
    owner_engine: Engine, households: tuple[Household, Household]
) -> None:
    a, _ = households
    with owner_engine.connect() as conn, conn.begin() as tx:
        conn.execute(text("ALTER TABLE public.rule NO FORCE ROW LEVEL SECURITY"))
        rid = _rule(conn, a, a.people[0])
        with pytest.raises(DBAPIError) as error, conn.begin_nested():
            conn.execute(
                table("rule")
                .update()
                .where(table("rule").c.id == rid)
                .values(author_person_id=a.people[1])
            )
        assert error.value.orig.sqlstate == "42501"
        tx.rollback()


@pytest.mark.parametrize("regex, cap", [(False, 200), (True, 25)])
def test_concurrent_sessions_cannot_exceed_a_cap(
    app_engine: Engine, households: tuple[Household, Household], regex: bool, cap: int
) -> None:
    a, _ = households
    alex, sam = a.people
    contender = sam if regex else alex
    with app_engine.begin() as conn:
        set_ctx(conn, a.id, alex)
        for _ in range(cap - 1):
            _rule(conn, a, alex, regex=regex, enabled=regex)

    def race(conn: Connection) -> str | None:
        try:
            _rule(conn, a, contender, regex=regex, enabled=regex)
        except DBAPIError as exc:
            return exc.orig.sqlstate
        return None

    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        app_engine.connect() as first,
        app_engine.connect() as second,
    ):
        second.execute(text("SET LOCAL statement_timeout = '10s'"))
        set_ctx(second, a.id, contender)
        second_pid = second.scalar(text("SELECT pg_backend_pid()"))
        with first.begin():
            set_ctx(first, a.id, alex)
            _rule(first, a, alex, regex=regex, enabled=regex)
            blocked: Future[str | None] = pool.submit(race, second)
            deadline = time.monotonic() + 5
            while not first.scalar(text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}):
                assert not blocked.done(), "second insert finished before the first committed"
                assert time.monotonic() < deadline, "second session did not wait on the cap lock"
                time.sleep(0.01)
        assert blocked.result(timeout=10) == "23514"

    with app_engine.begin() as conn:
        set_ctx(conn, a.id, contender)
        if regex:
            disabled = _rule(conn, a, contender, regex=True, enabled=False)
            with pytest.raises(DBAPIError) as error, conn.begin_nested():
                rule = table("rule")
                conn.execute(rule.update().where(rule.c.id == disabled).values(enabled=True))
            assert error.value.orig.sqlstate == "23514"
            assert not conn.scalar(text("SELECT app_household_wide()"))
        set_ctx(conn, a.id, wide=True)
        assert (
            conn.scalar(
                text("SELECT count(*) FROM rule WHERE enabled")
                if regex
                else text("SELECT count(*) FROM rule")
            )
            == cap
        )


def test_rule_delete_only_nulls_the_reference(
    app_engine: Engine, households: tuple[Household, Household]
) -> None:
    a, _ = households
    txn = table("transaction")
    with app_engine.begin() as conn:
        set_ctx(conn, a.id, a.people[0])
        rid = _rule(conn, a, a.people[0])
        tid = a.transactions["private"]
        update = txn.update().where(txn.c.id == tid)
        with pytest.raises(DBAPIError) as error, conn.begin_nested():
            conn.execute(update.values(category_rule_id=rid))
        assert error.value.orig.sqlstate == "23514"
        conn.execute(
            update.values(category_id=a.category, category_source="rule", category_rule_id=rid)
        )
        rule = table("rule")
        conn.execute(rule.delete().where(rule.c.id == rid))
        assert conn.execute(
            select(
                txn.c.household_id, txn.c.category_id, txn.c.category_source, txn.c.category_rule_id
            ).where(txn.c.id == tid)
        ).one() == (a.id, a.category, "rule", None)
