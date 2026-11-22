"""The statement-key resolver (ADR 0033 §4): a re-import after adoption lands on the same account.

Runs the real statement import as holdfast_app under RLS. All data is synthetic.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import create_session_factory
from app.db_context import scoped_session
from app.domain.statement_parse import ParsedStatement, detect_profile, parse_statement
from app.importers.profile_loader import load_profiles
from app.models import Account
from app.services.statement_import import DocumentMeta, import_statement
from tests.importers.test_statement_profiles import DEBIT
from tests.models.conftest import delete_household, set_ctx

pytestmark = pytest.mark.services


def _parse(raw: str) -> ParsedStatement:
    profile = detect_profile(raw, load_profiles())
    assert profile is not None
    return parse_statement(raw, profile)


def _meta(n: int) -> DocumentMeta:
    return DocumentMeta(
        sha256=f"{n:064x}",
        filename="statement.pdf",
        storage_path=f"statements/p4a-{n}.pdf",
        size_bytes=10,
    )


@pytest.fixture
def household(app_engine: Engine, owner_engine: Engine) -> Iterator[uuid.UUID]:
    with app_engine.begin() as c:
        hid = c.execute(
            text("SELECT create_household(:n)"), {"n": "Example Resolver Household"}
        ).scalar_one()
    yield hid
    delete_household(owner_engine, hid)


@pytest.fixture
async def factory(app_url: str, migrated: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(app_url)
    yield create_session_factory(engine)
    await engine.dispose()


async def test_a_statement_reimported_after_adoption_lands_on_the_same_account(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID, app_engine: Engine
) -> None:
    st = _parse(DEBIT)
    first = await import_statement(factory, household, st, _meta(1), household_wide=True)
    accounts = {b.account_id for b in first.blocks}
    assert accounts

    with app_engine.begin() as c:
        set_ctx(c, household, wide=True)
        conn = c.execute(
            text(
                "INSERT INTO bank_connection (household_id, provider, external_id,"
                " access_token_enc, env, status) VALUES"
                " (:h, 'plaid', :e, 'x', 'production', 'pending_setup') RETURNING id"
            ),
            {"h": household, "e": f"item-{uuid.uuid4()}"},
        ).scalar_one()
        for n, account_id in enumerate(sorted(accounts)):
            c.execute(
                text(
                    "UPDATE account SET source = 'plaid', bank_connection_id = :c,"
                    " external_account_id = :x, adopted_at = now() WHERE id = :a"
                ),
                {"c": conn, "x": f"plaid-account-{n}", "a": account_id},
            )

    # Another file with the same blocks (a re-downloaded statement): same accounts, no twins.
    again = await import_statement(factory, household, st, _meta(2), household_wide=True)
    assert {b.account_id for b in again.blocks} == accounts
    async with scoped_session(factory, household, None, household_wide=True) as s:
        assert (await s.execute(select(func.count()).select_from(Account))).scalar_one() == len(
            accounts
        )
        sources = set((await s.execute(select(Account.source))).scalars())
    assert sources == {"plaid"}


async def test_a_new_imported_account_carries_its_statement_key(
    factory: async_sessionmaker[AsyncSession], household: uuid.UUID
) -> None:
    await import_statement(factory, household, _parse(DEBIT), _meta(3), household_wide=True)
    async with scoped_session(factory, household, None, household_wide=True) as s:
        rows = (
            await s.execute(
                select(Account.source, Account.statement_key, Account.external_account_id)
            )
        ).all()
    assert rows
    assert all(src == "import" and key is not None and key == ext for src, key, ext in rows)
