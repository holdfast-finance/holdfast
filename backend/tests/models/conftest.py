"""Fixtures for schema and RLS tests.

The schema is migrated to head as ``holdfast_owner``; every assertion about data access runs as
``holdfast_app`` (no superuser, no BYPASSRLS), so RLS is enforced for real. All data is synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, Table, and_, create_engine, func, select, text
from sqlalchemy.sql.elements import ColumnElement

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.db import Base
from tests.conftest import (
    _reachable,
    _unavailable,
    env_database_url,
    env_migrations_database_url,
)

BACKEND = Path(__file__).resolve().parents[2]

# Every household-scoped table (household_id column + household_isolation policy).
HOUSEHOLD_TABLES = (
    "person",
    "household_setting",
    "bank_connection",
    "account",
    "balance_snapshot",
    "category",
    "rule",
    "transaction",
    "transaction_split",
    "holding",
    "investment_txn",
    "liability",
    "budget",
    "budget_line",
    "credit_report",
    "tradeline",
    "credit_inquiry",
    "credit_score_entry",
    "document",
    "parsed_field",
    "tax_year",
    "tax_item",
    "tax_scenario",
    "estimate_snapshot",
    "agent_conversation",
    "agent_message",
    "agent_finding",
    "job_run",
    "import_run",
    "sync_run",
)
# Connector tables (0011). Household-scoped and RLS-forced like the rest, but not seeded by
# ``seed_household``: their own suite is ``test_connector_link*.py`` (the slot ledger has no
# DELETE grant, so the generic DML tests don't apply to it).
CONNECTOR_TABLES = ("connector_account_link", "plaid_link_slot", "account_coverage")
IDENTITY_TABLES = ("app_user", "webauthn_credential", "auth_session", "totp_recovery_code")
REFERENCE_TABLES = ("institution", "security")
ACCOUNT_DETAIL_TABLES = (
    "transaction",
    "transaction_split",
    "holding",
    "investment_txn",
    "liability",
)
CONTRACT_TABLES = (
    "household",
    *HOUSEHOLD_TABLES,
    *CONNECTOR_TABLES,
    *IDENTITY_TABLES,
    *REFERENCE_TABLES,
    "audit_log",
)


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def where(t: Table, ident: dict[str, Any]) -> ColumnElement[bool]:
    return and_(*(t.c[k] == v for k, v in ident.items()))


def count(conn: Connection, name: str, ident: dict[str, Any] | None = None) -> int:
    t = table(name)
    stmt = select(func.count()).select_from(t)
    if ident:
        stmt = stmt.where(where(t, ident))
    return int(conn.execute(stmt).scalar_one())


def set_ctx(
    conn: Connection,
    household_id: uuid.UUID | None,
    person_id: uuid.UUID | None = None,
    *,
    wide: bool = False,
) -> None:
    """Transaction-local RLS context, exactly what ``app.db_context`` sets."""
    conn.execute(
        text(
            "SELECT set_config('app.household_id', :h, true),"
            " set_config('app.person_id', :p, true),"
            " set_config('app.household_wide', :w, true)"
        ),
        {
            "h": "" if household_id is None else str(household_id),
            "p": "" if person_id is None else str(person_id),
            "w": "on" if wide else "",
        },
    )


# --------------------------------------------------------------------------- engines


def _url(value: str | None, name: str) -> str:
    if not value:
        _unavailable(f"{name} is not set; start Postgres to run schema/RLS tests")
    assert value is not None
    if not _reachable(value, 5432):
        _unavailable(f"Postgres at {name} is unreachable")
    return value


@pytest.fixture(scope="session")
def app_url() -> str:
    return _url(env_database_url(), "TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def owner_url() -> str:
    return _url(env_migrations_database_url(), "TEST_MIGRATIONS_DATABASE_URL")


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.attributes["database_url"] = url
    cfg.attributes["skip_logging_config"] = True
    return cfg


@pytest.fixture(scope="module")
def migrated(owner_url: str) -> str:
    """Schema at head (idempotent). Returns the owner URL."""
    command.upgrade(alembic_config(owner_url), "head")
    return owner_url


@pytest.fixture(scope="module")
def app_engine(app_url: str, migrated: str) -> Iterator[Engine]:
    engine = create_engine(app_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == "holdfast_app"
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def owner_engine(migrated: str) -> Iterator[Engine]:
    engine = create_engine(migrated)
    yield engine
    engine.dispose()


# --------------------------------------------------------------------------- seed data


@dataclass
class Seed:
    household_id: uuid.UUID
    person_ids: list[uuid.UUID]
    # table -> identifying columns of one row in that table
    rows: dict[str, dict[str, Any]] = field(default_factory=dict)


def _insert(conn: Connection, name: str, /, **values: Any) -> Any:
    t = table(name)
    stmt = t.insert().values(**values)
    if "id" in t.c:
        return conn.execute(stmt.returning(t.c.id)).scalar_one()
    conn.execute(stmt)
    return None


def _reference_ids(conn: Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """Shared reference rows (no RLS), created once."""
    inst = conn.execute(
        text("SELECT id FROM institution WHERE name = 'First Example Bank'")
    ).scalar_one_or_none()
    if inst is None:
        inst = _insert(conn, "institution", name="First Example Bank")
    sec = conn.execute(text("SELECT id FROM security WHERE ticker = 'EXMPL'")).scalar_one_or_none()
    if sec is None:
        sec = _insert(conn, "security", ticker="EXMPL", name="Example Index Fund")
    return inst, sec


def seed_household(engine: Engine, label: str) -> Seed:
    """One row in every household-scoped table, inserted as holdfast_app under RLS.

    Runs household-wide, like a sync job: the account belongs to Alex, and only Alex or a
    household-wide job may write it (ADR 0008).
    """
    with engine.begin() as c:
        hid = c.execute(
            text("SELECT create_household(:n)"), {"n": f"Example Household {label}"}
        ).scalar_one()
        set_ctx(c, hid, wide=True)
        institution_id, security_id = _reference_ids(c)
        h = {"household_id": hid}
        p1 = _insert(c, "person", **h, display_name=f"Alex {label}", filing_role="primary")
        p2 = _insert(c, "person", **h, display_name=f"Sam {label}", filing_role="spouse")
        _insert(c, "household_setting", **h, key="ai.provider", value_enc=b"ciphertext")
        conn_id = _insert(
            c,
            "bank_connection",
            **h,
            institution_id=institution_id,
            provider="plaid",
            external_id=f"item-{uuid.uuid4()}",
            access_token_enc=b"ciphertext",
        )
        acct = _insert(
            c,
            "account",
            **h,
            bank_connection_id=conn_id,
            owner_person_id=p1,
            source="plaid",
            name="Example Checking",
            mask="0000",
            type="depository",
        )
        a = {"account_id": acct}
        _insert(
            c,
            "balance_snapshot",
            **h,
            **a,
            as_of_date=date(2026, 1, 31),
            current=Decimal("1234.56"),
        )
        cat = _insert(c, "category", **h, slug="custom.groceries", name="Groceries", kind="expense")
        rule = _insert(
            c,
            "rule",
            **h,
            author_person_id=p1,
            category_id=cat,
            name="Example Mart",
            conditions={"v": 1, "merchant": {"mode": "contains", "value": "EXAMPLE MART"}},
        )
        doc = _insert(
            c,
            "document",
            **h,
            person_id=p1,
            kind="receipt",
            filename="receipt.pdf",
            sha256=uuid.uuid4().hex * 2,
            # Relative to DOCUMENT_STORE. An absolute path fails ck_document_storage_path_relative.
            storage_path=f"receipts/{uuid.uuid4()}.pdf",
            mime="application/pdf",
            size_bytes=1024,
        )
        txn = _insert(
            c,
            "transaction",
            **h,
            **a,
            posted_date=date(2026, 1, 15),
            amount=Decimal("-42.10"),
            description_raw="EXAMPLE MART #0000",
            merchant_name="Example Mart",
            category_id=cat,
            receipt_document_id=doc,
            source="plaid",
        )
        split = _insert(
            c,
            "transaction_split",
            **h,
            transaction_id=txn,
            category_id=cat,
            amount=Decimal("-42.10"),
        )
        holding = _insert(
            c,
            "holding",
            **h,
            **a,
            security_id=security_id,
            as_of_date=date(2026, 1, 31),
            quantity=Decimal("10.5000000000"),
            value=Decimal("1050.00"),
        )
        inv = _insert(
            c,
            "investment_txn",
            **h,
            **a,
            date=date(2026, 1, 10),
            type="buy",
            security_id=security_id,
            quantity=Decimal("1.0000000000"),
            price=Decimal("100.00000000"),
            amount=Decimal("-100.00"),
        )
        _insert(c, "liability", **h, **a, kind="credit", apr=Decimal("19.9900"))
        budget = _insert(c, "budget", **h, name="Monthly", start_date=date(2026, 1, 1))
        line = _insert(
            c, "budget_line", **h, budget_id=budget, category_id=cat, amount=Decimal("500.00")
        )
        report = _insert(
            c, "credit_report", **h, person_id=p1, bureau="equifax", pulled_on=date(2026, 1, 2)
        )
        tradeline = _insert(
            c,
            "tradeline",
            **h,
            credit_report_id=report,
            creditor="Example Card Co",
            account_number_masked="XXXX0000",
        )
        inquiry = _insert(
            c,
            "credit_inquiry",
            **h,
            credit_report_id=report,
            creditor="Example Lender",
            date=date(2025, 12, 1),
            kind="hard",
        )
        score = _insert(
            c,
            "credit_score_entry",
            **h,
            person_id=p1,
            model="FICO 8",
            score=720,
            as_of=date(2026, 1, 2),
        )
        field_ = _insert(c, "parsed_field", **h, document_id=doc, key="total", value="42.10")
        tax_year = _insert(c, "tax_year", **h, year=2026)
        tax_item = _insert(
            c,
            "tax_item",
            **h,
            tax_year_id=tax_year,
            person_id=p1,
            form="W-2",
            line_key="box1",
            amount=Decimal("50000.00"),
            source_type="manual",
        )
        scenario = _insert(
            c, "tax_scenario", **h, tax_year_id=tax_year, name="Base", assumptions={}
        )
        estimate = _insert(
            c,
            "estimate_snapshot",
            **h,
            tax_year_id=tax_year,
            as_of=date(2026, 1, 31),
            federal_liability=Decimal("1000.00"),
            state_liability=Decimal("200.00"),
            withheld=Decimal("900.00"),
            projected_due=Decimal("300.00"),
        )
        convo = _insert(c, "agent_conversation", **h, title="Monthly review")
        msg = _insert(
            c, "agent_message", **h, conversation_id=convo, role="user", content={"text": "hi"}
        )
        finding = _insert(
            c, "agent_finding", **h, category="cashflow", title="Example", body_md="Synthetic."
        )
        job_run = _insert(
            c,
            "job_run",
            **h,
            job_name="example.noop",
            idempotency_key=f"seed-{label}",
            payload={"household_id": str(hid)},
            max_attempts=3,
        )
        import_run = _insert(c, "import_run", **h, document_id=doc, account_id=acct)
        sync_run = _insert(c, "sync_run", **h, bank_connection_id=conn_id)

    rows: dict[str, dict[str, Any]] = {
        "person": {"id": p1},
        "household_setting": {"household_id": hid, "key": "ai.provider"},
        "bank_connection": {"id": conn_id},
        "account": {"id": acct},
        "balance_snapshot": {"account_id": acct, "as_of_date": date(2026, 1, 31)},
        "category": {"id": cat},
        "rule": {"id": rule},
        "transaction": {"id": txn},
        "transaction_split": {"id": split},
        "holding": {"id": holding},
        "investment_txn": {"id": inv},
        "liability": {"account_id": acct},
        "budget": {"id": budget},
        "budget_line": {"id": line},
        "credit_report": {"id": report},
        "tradeline": {"id": tradeline},
        "credit_inquiry": {"id": inquiry},
        "credit_score_entry": {"id": score},
        "document": {"id": doc},
        "parsed_field": {"id": field_},
        "tax_year": {"id": tax_year},
        "tax_item": {"id": tax_item},
        "tax_scenario": {"id": scenario},
        "estimate_snapshot": {"id": estimate},
        "agent_conversation": {"id": convo},
        "agent_message": {"id": msg},
        "agent_finding": {"id": finding},
        "job_run": {"id": job_run},
        "import_run": {"id": import_run},
        "sync_run": {"id": sync_run},
    }
    assert set(rows) == set(HOUSEHOLD_TABLES)
    return Seed(household_id=hid, person_ids=[p1, p2], rows=rows)


def delete_household(owner_engine: Engine, household_id: uuid.UUID) -> None:
    """Cascade-delete a household as the schema owner, inside its own context.

    The app role has no DELETE on household (ADR 0008); FORCE RLS binds the owner too, so the
    context is still required.
    """
    with owner_engine.begin() as c:
        set_ctx(c, household_id, wide=True)
        c.execute(text("DELETE FROM household WHERE id = app_household_id()"))


@pytest.fixture(scope="module")
def two_households(app_engine: Engine, owner_engine: Engine) -> Iterator[tuple[Seed, Seed]]:
    a = seed_household(app_engine, "A")
    b = seed_household(app_engine, "B")
    yield a, b
    delete_household(owner_engine, a.household_id)
    delete_household(owner_engine, b.household_id)
