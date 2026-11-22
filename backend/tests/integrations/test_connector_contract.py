import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from inspect import getmembers
from uuid import UUID

import pytest

from app.domain.connector import ConnectorAccount, ConnectorBalance, ConnectorTxn, SyncBatch
from app.integrations.base import (
    BankConnector,
    ConnectionRef,
    ConnectionState,
    ConnectorError,
    LinkContext,
)
from app.integrations.fake import FakeConnector
from app.integrations.registry import ConnectorRegistry, registry
from app.security.secret_token import SecretToken

DAY = date(2026, 1, 15)
ACCOUNT = ConnectorAccount(
    external_account_id="account-example",
    name="Example credit card",
    official_name=None,
    mask="0000",
    type="credit",
    subtype=None,
    currency="USD",
    institution_name="First Example Bank",
)
BALANCE = ConnectorBalance(
    external_account_id=ACCOUNT.external_account_id,
    as_of=DAY,
    current=Decimal("-4.25"),
    available=Decimal("95.75"),
    limit=Decimal("100.00"),
)
CONNECTION = ConnectionRef(UUID(int=1), "synthetic-connection")
CONTEXT = LinkContext(household_id=UUID(int=2), user_id=UUID(int=3))
CREDENTIAL = SecretToken("synthetic-credential")


def _txn(
    external_id: str,
    amount: Decimal,
    *,
    is_pending: bool = False,
    pending_external_id: str | None = None,
) -> ConnectorTxn:
    return ConnectorTxn(
        external_account_id=ACCOUNT.external_account_id,
        external_id=external_id,
        posted_date=DAY,
        authorized_date=DAY - timedelta(days=1),
        amount=amount,
        description_raw="Example transaction",
        merchant_name=None,
        pfc_primary=None,
        pfc_detailed=None,
        is_pending=is_pending,
        pending_external_id=pending_external_id,
    )


@pytest.fixture
def connector() -> BankConnector:
    # The return annotation checks FakeConnector's full signature structurally in mypy.
    return FakeConnector(
        {
            None: SyncBatch(
                accounts=(ACCOUNT,),
                balances=(BALANCE,),
                added=(_txn("txn-pending", Decimal("-8.25"), is_pending=True),),
                modified=(),
                removed=(),
                next_cursor="page-2",
                history_complete=False,
            ),
            "page-2": SyncBatch(
                accounts=(),
                balances=(),
                added=(
                    _txn("txn-posted", Decimal("-8.25"), pending_external_id="txn-pending"),
                    _txn("txn-credit", Decimal("4.00")),
                ),
                modified=(_txn("txn-modified", Decimal("-1.50")),),
                removed=((ACCOUNT.external_account_id, "txn-removed"),),
                next_cursor=None,
                history_complete=True,
            ),
        }
    )


def test_fake_connector_satisfies_protocol_at_runtime() -> None:
    candidate: object = FakeConnector({})
    assert isinstance(candidate, BankConnector)


async def test_fake_cursor_replays_all_rows_once(connector: BankConnector) -> None:
    first = await connector.fetch(CONNECTION, CREDENTIAL, None)
    assert first.accounts == (ACCOUNT,)
    assert first.balances == (BALANCE,)
    assert first.next_cursor == "page-2"
    assert not first.history_complete

    second = await connector.fetch(CONNECTION, CREDENTIAL, first.next_cursor)
    assert second.next_cursor is None
    assert second.history_complete
    assert await connector.fetch(CONNECTION, CREDENTIAL, None) == first
    assert await connector.fetch(CONNECTION, CREDENTIAL, first.next_cursor) == second

    rows = first.added + first.modified + second.added + second.modified
    assert [row.external_id for row in rows] == [
        "txn-pending",
        "txn-posted",
        "txn-credit",
        "txn-modified",
    ]
    pending, posted, credit, modified = rows
    assert pending.is_pending and pending.pending_external_id is None
    assert not posted.is_pending and posted.pending_external_id == pending.external_id
    assert first.removed + second.removed == ((ACCOUNT.external_account_id, "txn-removed"),)
    assert all(isinstance(row.amount, Decimal) for row in rows)
    assert pending.amount == posted.amount == Decimal("-8.25")
    assert credit.amount == Decimal("4.00")
    assert modified.amount == Decimal("-1.50")
    assert isinstance(first.balances[0].current, Decimal)
    assert first.balances[0].current == Decimal("-4.25")


async def test_fake_replays_structured_errors_and_refuses_unknown_cursors() -> None:
    scripted = ConnectorError("retryable", "TEMPORARY", "request-example")
    fake = FakeConnector({None: scripted})
    for _ in range(2):
        with pytest.raises(ConnectorError) as error:
            await fake.fetch(CONNECTION, CREDENTIAL, None)
        assert (error.value.kind, error.value.code, error.value.request_id) == (
            "retryable",
            "TEMPORARY",
            "request-example",
        )
        assert error.value is not scripted
        assert str(error.value) == "Bank connector operation failed"

    with pytest.raises(ConnectorError) as unknown:
        await fake.fetch(CONNECTION, CREDENTIAL, "unknown-page")
    assert unknown.value.code == "UNSCRIPTED_CURSOR"


async def test_fake_links_and_relinks_with_opaque_credentials(connector: BankConnector) -> None:
    start = await connector.begin_link(CONTEXT)
    linked = await connector.complete_link(CONTEXT, SecretToken("synthetic-proof"))
    relink = await connector.begin_relink(CONTEXT, CONNECTION)

    assert isinstance(start.token, SecretToken)
    assert isinstance(relink.token, SecretToken)
    assert isinstance(linked.credential, SecretToken)
    assert linked.external_id == CONNECTION.external_id
    assert "synthetic-link" not in repr(start)
    assert "synthetic-relink" not in repr(relink)
    assert "synthetic-credential" not in repr(linked)


def test_fake_health_uses_supplied_time_and_state(connector: BankConnector) -> None:
    now = datetime(2026, 1, 15, tzinfo=UTC)
    state = ConnectionState(
        "active",
        last_success_at=now - timedelta(hours=48),
        last_error_at=now - timedelta(hours=49),
        error_code="TEMPORARY",
        post_relink_duplicates_removed=1,
    )
    health = connector.health(state, now)
    assert health.state == state and not health.stale
    assert connector.health(state, now + timedelta(seconds=1)).stale
    assert connector.health(ConnectionState("active"), now).stale


@pytest.fixture
def fake_registry() -> ConnectorRegistry:
    local = ConnectorRegistry()
    local.register(FakeConnector)
    return local


def test_registry_refuses_duplicate_and_unknown_provider(fake_registry: ConnectorRegistry) -> None:
    class Duplicate(FakeConnector):
        pass

    assert fake_registry.get("fake") is FakeConnector
    with pytest.raises(ValueError, match="already registered"):
        fake_registry.register(Duplicate)
    assert fake_registry.get("fake") is FakeConnector
    with pytest.raises(KeyError, match="unknown"):
        fake_registry.get("unknown")


def test_registry_override_restores_previous_entry_after_error(
    fake_registry: ConnectorRegistry,
) -> None:
    class Replacement(FakeConnector):
        pass

    with pytest.raises(RuntimeError, match="test error"), fake_registry.override(Replacement):
        assert fake_registry.get("fake") is Replacement
        raise RuntimeError("test error")
    assert fake_registry.get("fake") is FakeConnector

    empty = ConnectorRegistry()
    with empty.override(FakeConnector):
        assert empty.get("fake") is FakeConnector
    with pytest.raises(KeyError, match="fake"):
        empty.get("fake")


def test_connector_methods_cannot_expose_money_movement(fake_registry: ConnectorRegistry) -> None:
    forbidden = re.compile(
        r"transfer|payment|order|trad(?:e|ing)|withdraw|(?:send|move)_?money"
        r"|(?:^|_)(?:pay|buy|sell)(?:_|$)",
        re.IGNORECASE,
    )
    classes = (BankConnector, *registry, *fake_registry)
    matches = [
        f"{cls.__name__}.{name}"
        for cls in classes
        for name, _ in getmembers(cls, callable)
        if forbidden.search(name)
    ]
    assert not matches, f"Connector money movement methods: {matches}"
