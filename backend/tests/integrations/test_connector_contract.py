import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from inspect import getmembers
from pathlib import Path
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
from tests.integrations.helpers import override

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
                has_more=True,
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
                removed=(
                    (ACCOUNT.external_account_id, "txn-removed"),
                    (ACCOUNT.external_account_id, "txn-pending"),
                ),
                next_cursor="page-3",
                has_more=False,
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
    assert second.next_cursor == "page-3" and not second.has_more
    assert first.has_more
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
    assert first.removed + second.removed == (
        (ACCOUNT.external_account_id, "txn-removed"),
        (ACCOUNT.external_account_id, "txn-pending"),
    )
    # The removal of "txn-pending" is the pending row being posted by "txn-posted".
    posted_links = {row.pending_external_id for row in second.added}
    assert (ACCOUNT.external_account_id, "txn-pending") in second.removed
    assert "txn-pending" in posted_links
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


def test_override_helper_restores_previous_entry_after_error(
    fake_registry: ConnectorRegistry,
) -> None:
    class Replacement(FakeConnector):
        pass

    with pytest.raises(RuntimeError, match="test error"), override(fake_registry, Replacement):
        assert fake_registry.get("fake") is Replacement
        raise RuntimeError("test error")
    assert fake_registry.get("fake") is FakeConnector

    empty = ConnectorRegistry()
    with override(empty, FakeConnector):
        assert empty.get("fake") is FakeConnector
    with pytest.raises(KeyError, match="fake"):
        empty.get("fake")


PROTOCOL_SURFACE = {
    "begin_link",
    "complete_link",
    "begin_relink",
    "fetch",
    "health",
}


def _public_callables(cls: type) -> set[str]:
    return {name for name, _ in getmembers(cls, callable) if not name.startswith("_")}


def test_protocol_surface_is_the_documented_set() -> None:
    assert _public_callables(BankConnector) == PROTOCOL_SURFACE


def test_registered_connectors_expose_exactly_the_protocol(
    fake_registry: ConnectorRegistry,
) -> None:
    for cls in (*registry, *fake_registry):
        assert _public_callables(cls) == PROTOCOL_SURFACE, cls.__name__


def test_extra_public_method_fails_the_allowlist(fake_registry: ConnectorRegistry) -> None:
    class Sneaky(FakeConnector):
        provider = "sneaky"

        def rebalance(self) -> None: ...

    assert _public_callables(Sneaky) != PROTOCOL_SURFACE


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


def test_frozen_registry_refuses_register(fake_registry: ConnectorRegistry) -> None:
    class Late(FakeConnector):
        provider = "late"

    fake_registry.freeze()
    fake_registry.freeze()  # idempotent
    with pytest.raises(RuntimeError, match="frozen"):
        fake_registry.register(Late)
    assert not hasattr(fake_registry, "override")


def test_startup_freezes_the_production_registry() -> None:

    root = Path(__file__).resolve().parents[2] / "app"
    for rel in ("main.py", "worker/main.py"):
        assert "connector_registry.freeze()" in (root / rel).read_text(), rel


def test_registry_mutation_only_in_registry_startup_and_tests() -> None:
    root = Path(__file__).resolve().parents[2] / "app"
    call = re.compile(r"\bregistry\s*\.\s*(register|override)\s*\(")
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if path.relative_to(root).as_posix() not in {"integrations/registry.py", "main.py"}
        and not path.relative_to(root).as_posix().startswith("worker/")
        and call.search(path.read_text())
    ]
    assert not offenders, f"registry mutated outside startup: {offenders}"


# Money values.


def _balance(**kwargs: Decimal | None) -> ConnectorBalance:
    base: dict[str, Decimal | None] = {
        "current": Decimal("0.00"),
        "available": None,
        "limit": None,
    }
    base.update(kwargs)
    return ConnectorBalance(ACCOUNT.external_account_id, DAY, **base)


def test_money_range_boundary() -> None:
    assert _txn("a", Decimal("9999999999.99")).amount == Decimal("9999999999.99")
    assert _txn("a", Decimal("-9999999999.99")).amount == Decimal("-9999999999.99")
    for bad in ("10000000000.00", "-10000000000.00", "1E+30"):
        with pytest.raises(ValueError, match="out of range"):
            _txn("a", Decimal(bad))
        with pytest.raises(ValueError, match="out of range"):
            _balance(current=Decimal(bad))


def test_money_is_stored_in_canonical_two_place_form() -> None:
    assert str(_txn("a", Decimal("-8.2")).amount) == "-8.20"
    assert str(_txn("a", Decimal("1E+2")).amount) == "100.00"
    assert str(_txn("a", Decimal("-0.00")).amount) == "0.00"
    assert str(_txn("a", Decimal("-0")).amount) == "0.00"
    balance = _balance(current=Decimal("-4.5"), available=Decimal("5"), limit=Decimal("1E+2"))
    assert (str(balance.current), str(balance.available), str(balance.limit)) == (
        "-4.50",
        "5.00",
        "100.00",
    )


def test_money_refuses_floats_nan_and_extra_places() -> None:
    for bad in (1.5, "1.00", Decimal("NaN"), Decimal("Infinity"), Decimal("0.001")):
        with pytest.raises((TypeError, ValueError)):
            _txn("a", bad)  # type: ignore[arg-type]


def test_currency_is_iso_shape_or_none() -> None:
    assert replace(ACCOUNT, currency=None).currency is None
    for bad in ("usd", "US", "USDX", "", "U$D", " USD"):
        with pytest.raises(ValueError, match="currency"):
            replace(ACCOUNT, currency=bad)


def test_credit_fixture_balance_relation_and_limit_sign() -> None:
    assert BALANCE.available == BALANCE.limit + BALANCE.current  # type: ignore[operator]
    with pytest.raises(ValueError, match="limit"):
        _balance(limit=Decimal("-1.00"))


def test_txn_id_rules() -> None:
    with pytest.raises(ValueError, match="external_id"):
        _txn("", Decimal("1.00"))
    with pytest.raises(ValueError, match="pending_external_id"):
        _txn("same", Decimal("1.00"), pending_external_id="same")
    with pytest.raises(ValueError, match="pending_external_id"):
        _txn("a", Decimal("1.00"), pending_external_id="")
    with pytest.raises(ValueError, match="pending"):
        _txn("a", Decimal("1.00"), is_pending=True, pending_external_id="b")


def test_has_more_requires_a_cursor() -> None:
    with pytest.raises(ValueError, match="has_more"):
        SyncBatch((), (), (), (), (), None, True, False)


def test_sensitive_text_is_not_in_repr() -> None:
    txn = _txn("a", Decimal("1.00"))
    named = replace(ACCOUNT, name="Secret Name", official_name="Secret Official")
    assert "Example transaction" not in repr(txn)
    assert "Secret" not in repr(named)


# Error shape.


def test_connector_error_refuses_provider_free_text() -> None:
    for kind, code, request_id in (
        ("Item login required", "TEMPORARY", None),
        ("retryable", "item login required", None),
        ("retryable", "", None),
        ("retryable", "A" * 65, None),
        ("retryable", "TEMPORARY", "request id with spaces"),
        ("retryable", "TEMPORARY", "x" * 65),
        ("retryable", "TEMPORARY", ""),
    ):
        with pytest.raises(ValueError):
            ConnectorError(kind, code, request_id)
    ConnectorError("auth_required", "ITEM_LOGIN_REQUIRED", "Abc-123")


def test_connection_state_error_code_is_validated() -> None:
    assert ConnectionState("active", error_code="TEMPORARY").error_code == "TEMPORARY"
    with pytest.raises(ValueError, match="code"):
        ConnectionState("active", error_code="Bank said: no")
