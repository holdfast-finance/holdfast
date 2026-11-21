"""Create, sync, list and delete watch-only wallets against real Postgres under RLS.

All keys and addresses are published BIP84 test vectors or invented. Nothing private is used.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.crypto import bitcoin
from app.domain.crypto.errors import SecretMaterialError
from app.integrations.crypto.chains.base import ChainTransfer
from app.integrations.crypto.chains.fake import FakeChainSource
from app.models import AuditLog, CryptoAddress, CryptoTx, CryptoWallet
from app.schemas.crypto import WalletCreate
from app.security import crypto as fernet
from app.services.crypto_wallets import CryptoWalletService, sync_wallet
from app.services.errors import ApiError
from tests.crypto.conftest import Fixture, context, principal

pytestmark = pytest.mark.services

ZPUB = "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs"  # noqa: E501
FP = "73c5da0a"
FIRST = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
EVM = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
NOW = datetime(2026, 1, 1, tzinfo=UTC)
Factory = async_sessionmaker[AsyncSession]


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FERNET_KEY", fernet.generate_key())
    from app.config import get_settings

    get_settings.cache_clear()
    yield  # type: ignore[misc]
    get_settings.cache_clear()


def tx(
    address: str, quantity: str, n: int = 1, *, height: int | None = 100, **kw: object
) -> ChainTransfer:
    qty = Decimal(quantity)
    base = {
        "tx_hash": f"{n:064x}",
        "address": address,
        "direction": "in" if qty >= 0 else "out",
        "asset": "BTC",
        "asset_contract": "",
        "quantity": qty,
        "event_index": 0,
        "block_height": height,
        "block_time": NOW if height else None,
    }
    return ChainTransfer(**{**base, **kw})  # type: ignore[arg-type]


def svc(factory: Factory, world: Fixture, person: uuid.UUID | None = None) -> CryptoWalletService:
    return CryptoWalletService(context(factory), principal(world, person or world.alex))


def body(**over: object) -> WalletCreate:
    base = {
        "label": "Cold storage",
        "chain": "bitcoin",
        "key_or_address": f"[{FP}/84h/0h/0h]{ZPUB}",
    }
    return WalletCreate.model_validate({**base, **over})


async def rows(factory: Factory, world: Fixture, model: type) -> int:
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        return int((await s.execute(select(func.count()).select_from(model))).scalar_one())


def derived(change: bool, index: int) -> str:
    key = bitcoin.parse_watch_key(ZPUB)
    return bitcoin.derive_addresses(key, change=change, start=index, count=1)[0].address


# ------------------------------------------------------------------ create


async def test_create_xpub_wallet_encrypts_the_key_and_derives_both_branches(
    factory: Factory, world: Fixture
) -> None:
    out = await svc(factory, world).create(body(gap_limit=5))
    assert (out.kind, out.script_type, out.fingerprint) == ("watch_xpub", "p2wpkh", FP)
    assert (out.derivation_path, out.address_count, out.status) == ("m/84h/0h/0h", 10, "active")
    assert out.first_receive_address == FIRST
    assert "zpub" not in out.model_dump_json() and "descriptor" not in out.model_dump_json()
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        wallet = (await s.execute(select(CryptoWallet))).scalar_one()
        assert wallet.descriptor_enc is not None
        assert b"xpub" not in wallet.descriptor_enc and b"zpub" not in wallet.descriptor_enc
        assert fernet.decrypt(wallet.descriptor_enc.decode("ascii")).startswith(
            f"wpkh([{FP}/84h/0h/0h]xpub"
        )
        assert wallet.is_watch_only is True and wallet.owner_person_id == world.alex


async def test_audit_names_the_action_but_never_a_key_or_address(
    factory: Factory, world: Fixture
) -> None:
    await svc(factory, world).create(body(gap_limit=2))
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        entry = (
            await s.execute(select(AuditLog).where(AuditLog.action == "crypto.wallet.created"))
        ).scalar_one()
    dump = f"{entry.detail} {entry.entity} {entry.entity_id}"
    assert entry.detail["addresses"] == 4
    assert "pub6" not in dump and FIRST not in dump


async def test_the_same_wallet_cannot_be_added_twice_even_in_another_spelling(
    factory: Factory, world: Fixture
) -> None:
    service = svc(factory, world)
    await service.create(body())
    for spelling in (ZPUB, f"wpkh([{FP}/84h/0h/0h]{ZPUB}/0/*)"):
        with pytest.raises(ApiError) as err:
            await service.create(body(key_or_address=spelling))
        assert err.value.code == "conflict"
    assert await rows(factory, world, CryptoWallet) == 1


@pytest.mark.parametrize(
    "secret",
    [
        "xprv" + "A" * 107,
        "5" + "H" * 50,
        "ab" * 32,
        " ".join(["lorem", "ipsum", "dolor", "sit"] * 3),
    ],
)
async def test_secret_looking_input_is_refused_stored_nowhere_and_not_echoed(
    factory: Factory, world: Fixture, secret: str
) -> None:
    service = svc(factory, world)
    for field in ("key_or_address", "label"):
        with pytest.raises(ApiError) as err:
            await service.create(body(**{field: secret}))
        assert err.value.code == "validation_error"
        assert err.value.message == SecretMaterialError.MESSAGE
        assert secret not in err.value.message
    assert await rows(factory, world, CryptoWallet) == 0
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        assert (await s.execute(select(func.count()).select_from(AuditLog))).scalar_one() == 0


async def test_evm_address_wallet_is_checksummed_and_keyed_case_insensitively(
    factory: Factory, world: Fixture
) -> None:
    service = svc(factory, world)
    out = await service.create(body(chain="base", key_or_address=EVM.lower()))
    assert (out.kind, out.chain, out.address_count, out.first_receive_address) == (
        "watch_address",
        "base",
        1,
        EVM,
    )
    with pytest.raises(ApiError) as err:
        await service.create(body(chain="base", key_or_address=EVM.upper().replace("0X", "0x")))
    assert err.value.code == "conflict"
    # The same address on another chain is a different wallet.
    await service.create(body(chain="polygon", key_or_address=EVM))


@pytest.mark.parametrize(
    ("chain", "value"),
    [
        ("solana", FIRST),
        ("ethereum", FIRST),  # a Bitcoin address on an EVM chain
        ("bitcoin", EVM),
        ("ethereum", ZPUB),  # xpubs are Bitcoin only
    ],
)
async def test_wrong_chain_or_unsupported_input_is_a_validation_error(
    factory: Factory, world: Fixture, chain: str, value: str
) -> None:
    with pytest.raises(ApiError) as err:
        await svc(factory, world).create(body(chain=chain, key_or_address=value))
    assert err.value.code == "validation_error" and value not in err.value.message


async def test_a_bitcoin_address_wallet(factory: Factory, world: Fixture) -> None:
    out = await svc(factory, world).create(body(key_or_address=FIRST.upper()))
    assert (out.kind, out.address_count, out.first_receive_address) == ("watch_address", 1, FIRST)


# ------------------------------------------------------------------ sync


async def make_wallet(factory: Factory, world: Fixture, **over: object) -> uuid.UUID:
    return (await svc(factory, world).create(body(gap_limit=5, **over))).id


async def run_sync(factory: Factory, world: Fixture, wallet: uuid.UUID, source: FakeChainSource):  # type: ignore[no-untyped-def]
    return await sync_wallet(factory, world.household_id, world.alex, wallet, source)


async def test_sync_stores_signed_exact_quantities_and_reconciles(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    a, b = derived(False, 0), derived(True, 0)
    source = FakeChainSource(
        "bitcoin",
        [
            tx(a, "0.5", 1),
            tx(a, "-0.50000000", 2, height=101),
            tx(b, "0.19990001", 2, height=101, event_index=1),
        ],
    )
    result = await run_sync(factory, world, wallet, source)
    assert (result.outcome, result.inserted, result.updated, result.rejected) == (
        "reconciled",
        3,
        0,
        0,
    )
    out = await svc(factory, world).get(wallet)
    assert out.status == "active" and out.last_synced_at is not None
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        got = {
            (t.address, t.direction): t.quantity
            for t in (await s.execute(select(CryptoTx))).scalars()
        }
        assert got[(a, "in")] == Decimal("0.5")
        assert got[(a, "out")] == Decimal("-0.5")
        assert got[(b, "in")] == Decimal("0.19990001")
        first_seen = (
            await s.execute(select(CryptoAddress.first_seen).where(CryptoAddress.address == a))
        ).scalar_one()
        assert first_seen == NOW


async def test_a_second_sync_changes_nothing(factory: Factory, world: Fixture) -> None:
    wallet = await make_wallet(factory, world)
    source = FakeChainSource("bitcoin", [tx(derived(False, 0), "1")])
    await run_sync(factory, world, wallet, source)
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        before = (await s.execute(select(CryptoTx.id, CryptoTx.updated_at))).all()
    again = await run_sync(factory, world, wallet, source)
    assert (again.inserted, again.updated, again.outcome) == (0, 0, "reconciled")
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        assert (await s.execute(select(CryptoTx.id, CryptoTx.updated_at))).all() == before


async def test_pending_becomes_confirmed_without_touching_the_users_label(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    a = derived(False, 0)
    await run_sync(factory, world, wallet, FakeChainSource("bitcoin", [tx(a, "1", height=None)]))
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        await s.execute(text("UPDATE crypto_tx SET label = 'Salary', classification = 'gift'"))
    result = await run_sync(
        factory, world, wallet, FakeChainSource("bitcoin", [tx(a, "1", height=120)])
    )
    assert (result.inserted, result.updated) == (0, 1)
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        row = (await s.execute(select(CryptoTx))).scalar_one()
    assert (row.block_height, row.label, row.classification) == (120, "Salary", "gift")


async def test_gap_limit_discovery_extends_until_the_gap_is_clear(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)  # gap limit 5
    inside_gap, found_after_extending, beyond = (
        derived(False, 3),
        derived(False, 8),
        derived(False, 15),
    )
    source = FakeChainSource(
        "bitcoin",
        [tx(inside_gap, "1", 1), tx(found_after_extending, "1", 2), tx(beyond, "1", 3)],
    )
    result = await run_sync(factory, world, wallet, source)
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        receive = (
            await s.execute(
                select(func.max(CryptoAddress.address_index)).where(
                    CryptoAddress.is_change.is_(False)
                )
            )
        ).scalar_one()
        seen = {t.address for t in (await s.execute(select(CryptoTx))).scalars()}
    assert receive == 8 + 5  # the last used index plus the gap
    assert seen == {inside_gap, found_after_extending}  # index 15 is past the gap: not found
    assert result.addresses == receive + 1 + 5


async def test_a_balance_the_history_cannot_explain_needs_review(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    a = derived(False, 0)
    source = FakeChainSource(
        "bitcoin", [tx(a, "1")], extra_balance={(a, "BTC", ""): Decimal("0.00000001")}
    )
    result = await run_sync(factory, world, wallet, source)
    assert result.outcome == "needs_review"
    assert (await svc(factory, world).get(wallet)).status == "needs_review"
    # Once the chain and the history agree again, the wallet reconciles.
    assert (
        await run_sync(factory, world, wallet, FakeChainSource("bitcoin", [tx(a, "1")]))
    ).outcome == "reconciled"
    assert (await svc(factory, world).get(wallet)).status == "active"


class LeakySource(FakeChainSource):
    """A misbehaving source that returns rows for addresses it was not asked about."""

    async def history(self, addresses, *, since_height=None):  # type: ignore[no-untyped-def]
        return list(self.transfers)


async def test_values_the_column_cannot_hold_and_foreign_rows_are_rejected_not_rounded(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    a = derived(False, 0)
    source = LeakySource(
        "bitcoin",
        [
            tx(a, "1", 1),
            tx(a, "0.0000000000000000001", 2),  # 19 decimals
            tx(a, "100000000000000000000", 3),  # 21 integer digits
            tx(a, "-1", 4, direction="in"),  # wrong sign for its direction
            tx("37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf", "1", 5),  # not ours
            tx(a, "1", 6, tx_hash="not-a-hash"),
        ],
    )
    result = await run_sync(factory, world, wallet, source)
    assert (result.inserted, result.rejected, result.outcome) == (1, 5, "needs_review")
    assert await rows(factory, world, CryptoTx) == 1


async def test_a_node_that_cannot_list_history_is_reported_not_guessed(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    result = await run_sync(
        factory, world, wallet, FakeChainSource("bitcoin", history_unavailable=True)
    )
    assert (result.outcome, result.inserted) == ("history_unavailable", 0)
    assert (await svc(factory, world).get(wallet)).status == "needs_review"


async def test_a_failing_source_marks_the_wallet_error_and_audits_the_failure(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    result = await run_sync(factory, world, wallet, FakeChainSource("bitcoin", fail=True))
    assert result.outcome == "failed"
    out = await svc(factory, world).get(wallet)
    assert out.status == "error" and out.last_synced_at is None
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        entry = (
            await s.execute(select(AuditLog).where(AuditLog.action == "crypto.wallet.synced"))
        ).scalar_one()
    assert entry.detail["outcome"] == "failure" and "chain source" not in str(entry.detail)


async def test_evm_native_and_token_quantities_keep_18_decimals(
    factory: Factory, world: Fixture
) -> None:
    wallet = (await svc(factory, world).create(body(chain="ethereum", key_or_address=EVM))).id
    contract = "0xdbf03b407c01e7cd3cbea99509d93f8dddc8c6fb"
    source = FakeChainSource(
        "ethereum",
        [
            tx(EVM, "1.000000000000000001", 1, asset="ETH"),
            tx(
                EVM,
                "-0.5",
                2,
                asset="ETH",
                fee_asset="ETH",
                fee_qty=Decimal("0.000000000000000001"),
            ),
            tx(EVM, "12.5", 3, asset="USDC", asset_contract=contract),
        ],
    )
    result = await run_sync(factory, world, wallet, source)
    assert result.outcome == "reconciled"
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        eth = (
            await s.execute(select(func.sum(CryptoTx.quantity)).where(CryptoTx.asset == "ETH"))
        ).scalar_one()
    assert eth == Decimal("0.500000000000000001")


# ------------------------------------------------------------------ reading and visibility


async def test_private_wallets_are_invisible_to_the_other_person(
    factory: Factory, world: Fixture
) -> None:
    mine = await svc(factory, world).create(body(visibility="private"))
    other = svc(factory, world, world.sam)
    assert [w.id for w in (await other.list()).items] == []
    with pytest.raises(ApiError) as err:
        await other.get(mine.id)
    assert err.value.code == "not_found"
    with pytest.raises(ApiError):
        await other.delete(mine.id)
    assert await rows(factory, world, CryptoWallet) == 1


async def test_shared_wallets_are_visible_but_read_only_for_the_other_person(
    factory: Factory, world: Fixture
) -> None:
    mine = await svc(factory, world).create(body(visibility="shared"))
    other = svc(factory, world, world.sam)
    assert [w.id for w in (await other.list()).items] == [mine.id]
    with pytest.raises(ApiError) as err:
        await other.delete(mine.id)  # visible, but not writable for them (ADR 0008)
    assert err.value.code == "forbidden"
    assert await rows(factory, world, CryptoWallet) == 1


async def test_delete_removes_addresses_and_transactions_and_audits(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    await run_sync(factory, world, wallet, FakeChainSource("bitcoin", [tx(derived(False, 0), "1")]))
    await svc(factory, world).delete(wallet)
    for model in (CryptoWallet, CryptoAddress, CryptoTx):
        assert await rows(factory, world, model) == 0
    async with scoped_session(factory, world.household_id, None, household_wide=True) as s:
        actions = set((await s.execute(select(AuditLog.action))).scalars())
    assert {"crypto.wallet.created", "crypto.wallet.synced", "crypto.wallet.deleted"} <= actions


async def test_transactions_page_newest_first_with_pending_on_top(
    factory: Factory, world: Fixture
) -> None:
    wallet = await make_wallet(factory, world)
    a = derived(False, 0)
    when = [datetime(2026, 1, d, tzinfo=UTC) for d in (1, 2, 3, 4)]
    transfers = [tx(a, "1", n + 1, block_time=when[n]) for n in range(4)] + [
        tx(a, "1", 9, height=None)
    ]
    await run_sync(factory, world, wallet, FakeChainSource("bitcoin", transfers))
    service = svc(factory, world)
    first = await service.transactions(wallet, limit=2)
    assert first.items[0].block_time is None  # pending first
    assert first.items[1].block_time == when[3] and first.next_cursor is not None
    second = await service.transactions(wallet, limit=2, before=first.next_cursor)
    assert [t.block_time for t in second.items] == [when[2], when[1]]
    third = await service.transactions(wallet, limit=2, before=second.next_cursor)
    assert [t.block_time for t in third.items] == [when[0]] and third.next_cursor is None
    assert all(isinstance(t.quantity, Decimal) for t in first.items)
    with pytest.raises(ApiError):
        await service.transactions(wallet, before="garbage")
