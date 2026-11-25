"""Fixtures for service tests that need real Postgres as holdfast_app (RLS enforced)."""

from __future__ import annotations

from tests.ledger import conftest as ledger_conftest

app_url = ledger_conftest.app_url
owner_url = ledger_conftest.owner_url
migrated = ledger_conftest.migrated
app_engine = ledger_conftest.app_engine
owner_engine = ledger_conftest.owner_engine
household = ledger_conftest.household
factory = ledger_conftest.factory
