"""Crypto Track schema (ADR 0020): constraints, exact quantities, idempotent keys, per-person
visibility. Runs as holdfast_app under RLS. All data is synthetic; the one address is the published
BIP84 test vector.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError, IntegrityError, StatementError

from tests.models.conftest import _insert, delete_household, set_ctx, table

pytestmark = pytest.mark.services

ADDRESS = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"
OTHER_ADDRESS = "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"
TXID = "cd" * 32
VISIBILITIES = ("private", "summary_only", "shared", "joint")


@dataclass
class CryptoHousehold:
    household_id: uuid.UUID
    alex: uuid.UUID
    sam: uuid.UUID
    wallets: dict[str, uuid.UUID]  # visibility kind -> wallet id
    exchanges: dict[str, uuid.UUID]


def wallet_values(h: uuid.UUID, owner: uuid.UUID | None, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "household_id": h,
        "owner_person_id": owner,
        "label": "Example wallet",
        "chain": "bitcoin",
        "kind": "watch_address",
        "identity_hash": uuid.uuid4().hex * 2,
    }
    return {**base, **over}


def tx_values(h: uuid.UUID, wallet: uuid.UUID, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "household_id": h,
        "wallet_id": wallet,
        "chain": "bitcoin",
        "tx_hash": TXID,
        "address": ADDRESS,
        "direction": "in",
        "asset": "BTC",
        "quantity": Decimal("0.00100000"),
    }
    return {**base, **over}


def exchange_values(h: uuid.UUID, owner: uuid.UUID | None, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "household_id": h,
        "owner_person_id": owner,
        "exchange_id": "example",
        "label": f"Example {uuid.uuid4().hex[:6]}",
        "api_key_enc": b"ciphertext",
        "api_secret_enc": b"ciphertext",
        "permissions": {"read": True, "trade": False, "withdraw": False, "reported": True},
    }
    return {**base, **over}


@pytest.fixture(scope="module")
def household(app_engine: Engine, owner_engine: Engine) -> Iterator[CryptoHousehold]:
    with app_engine.begin() as c:
        hid = c.execute(text("SELECT create_household('Example Crypto')")).scalar_one()
        set_ctx(c, hid, wide=True)
        alex = _insert(c, "person", household_id=hid, display_name="Alex", filing_role="primary")
        sam = _insert(c, "person", household_id=hid, display_name="Sam", filing_role="spouse")
        wallets: dict[str, uuid.UUID] = {}
        exchanges: dict[str, uuid.UUID] = {}
        for kind in VISIBILITIES:
            owner = None if kind == "joint" else alex
            vis = "private" if kind == "joint" else kind
            wallet = _insert(c, "crypto_wallet", **wallet_values(hid, owner, visibility=vis))
            wallets[kind] = wallet
            _insert(
                c,
                "crypto_address",
                household_id=hid,
                wallet_id=wallet,
                address=f"{kind}-{ADDRESS}",
            )
            _insert(c, "crypto_tx", **tx_values(hid, wallet))
            exchange = _insert(c, "exchange_account", **exchange_values(hid, owner, visibility=vis))
            exchanges[kind] = exchange
            _insert(
                c,
                "crypto_tx",
                household_id=hid,
                exchange_account_id=exchange,
                external_id=f"trade-{kind}",
                direction="in",
                asset="BTC",
                quantity=Decimal("1"),
            )
    yield CryptoHousehold(hid, alex, sam, wallets, exchanges)
    delete_household(owner_engine, hid)


def count_as(engine: Engine, h: CryptoHousehold, person: uuid.UUID, name: str, **match: Any) -> int:
    t = table(name)
    with engine.begin() as c:
        set_ctx(c, h.household_id, person)
        stmt = t.select().with_only_columns(text("count(*)")).select_from(t)
        for key, value in match.items():
            stmt = stmt.where(t.c[key] == value)
        return int(c.execute(stmt).scalar_one())


# --------------------------------------------------------------------------- constraints


def expect_check(engine: Engine, h: CryptoHousehold, name: str, values: dict[str, Any]) -> None:
    with engine.connect() as c:
        trans = c.begin()
        set_ctx(c, h.household_id, wide=True)
        with pytest.raises((IntegrityError, DBAPIError)) as err:
            c.execute(table(name).insert().values(**values))
        trans.rollback()
    assert "violates" in str(err.value)


@pytest.mark.parametrize(
    ("name", "over"),
    [
        ("crypto_wallet", {"is_watch_only": False}),
        ("crypto_wallet", {"kind": "watch_xpub", "chain": "ethereum", "descriptor_enc": b"x"}),
        ("crypto_wallet", {"kind": "watch_xpub"}),  # a key kind needs a key
        ("crypto_wallet", {"descriptor_enc": b"x"}),  # an address wallet has none
        ("crypto_wallet", {"fingerprint": "ABCDEF12"}),
        ("crypto_wallet", {"kind": "spend_descriptor"}),
        ("crypto_wallet", {"gap_limit": 0}),
        ("crypto_wallet", {"gap_limit": 1000}),
        ("crypto_wallet", {"fingerprint": "0a0b0c0d"}),  # address wallet has no key metadata
    ],
)
def test_wallet_checks_reject(
    app_engine: Engine, household: CryptoHousehold, name: str, over: dict[str, Any]
) -> None:
    expect_check(app_engine, household, name, wallet_values(household.household_id, None, **over))


@pytest.mark.parametrize(
    "permissions",
    [
        {"read": True, "trade": True},
        {"read": True, "withdraw": True},
        {"read": True, "trade": "true"},
        ["read"],
    ],
)
def test_exchange_key_can_never_record_trade_or_withdraw(
    app_engine: Engine, household: CryptoHousehold, permissions: Any
) -> None:
    values = exchange_values(household.household_id, None, permissions=permissions)
    expect_check(app_engine, household, "exchange_account", values)


@pytest.mark.parametrize(
    "over",
    [
        {"direction": "in", "quantity": Decimal("-1")},
        {"direction": "out", "quantity": Decimal("1")},
        {"direction": "sideways"},
        {"fee_asset": "BTC"},  # fee asset without a quantity
        {"fee_asset": "BTC", "fee_qty": Decimal("-0.1")},
        {"tx_hash": "XYZ"},
        {"asset": "btc"},
        {"asset_contract": "0xABCDEF"},
        {"event_index": -1},
        {"raw": ["not", "an", "object"]},
    ],
)
def test_crypto_tx_checks_reject(
    app_engine: Engine, household: CryptoHousehold, over: dict[str, Any]
) -> None:
    values = tx_values(household.household_id, household.wallets["shared"], **over)
    expect_check(app_engine, household, "crypto_tx", values)


def test_crypto_tx_needs_exactly_one_parent(app_engine: Engine, household: CryptoHousehold) -> None:
    h = household.household_id
    neither = tx_values(h, household.wallets["shared"], wallet_id=None)
    both = tx_values(
        h, household.wallets["shared"], exchange_account_id=household.exchanges["shared"]
    )
    for values in (neither, both):
        expect_check(app_engine, household, "crypto_tx", values)


def test_signed_quantity_keeps_all_18_decimals(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    wei = Decimal("-123456789.123456789012345678")
    values = tx_values(
        household.household_id,
        household.wallets["shared"],
        tx_hash="0x" + "ef" * 32,
        direction="out",
        quantity=wei,
        fee_asset="ETH",
        fee_qty=Decimal("0.000000000000000001"),
    )
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, wide=True)
        row_id = _insert(c, "crypto_tx", **values)
        t = table("crypto_tx")
        row = c.execute(t.select().where(t.c.id == row_id)).mappings().one()
        assert row["quantity"] == wei
        assert row["fee_qty"] == Decimal("0.000000000000000001")
        c.execute(t.delete().where(t.c.id == row_id))


def test_quantity_that_does_not_fit_is_rejected_not_rounded(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    values = tx_values(
        household.household_id, household.wallets["shared"], quantity=Decimal(10) ** 21
    )
    with app_engine.connect() as c:
        trans = c.begin()
        set_ctx(c, household.household_id, wide=True)
        with pytest.raises((DBAPIError, StatementError)):
            c.execute(table("crypto_tx").insert().values(**values))
        trans.rollback()


# --------------------------------------------------------------------------- idempotency


def test_same_movement_twice_changes_nothing_and_second_output_is_distinct(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    t = table("crypto_tx")
    base = tx_values(household.household_id, household.wallets["shared"], tx_hash="0x" + "11" * 32)
    elements = [
        "wallet_id",
        "chain",
        "tx_hash",
        "address",
        "direction",
        "asset",
        "asset_contract",
        "event_index",
    ]

    def upsert(c: Connection, **over: Any) -> int:
        stmt = (
            pg_insert(t)
            .values(**{**base, **over})
            .on_conflict_do_nothing(
                index_elements=elements, index_where=text("wallet_id IS NOT NULL")
            )
        )
        return len(c.execute(stmt.returning(t.c.id)).all())

    with app_engine.begin() as c:
        set_ctx(c, household.household_id, wide=True)
        assert upsert(c) == 1
        assert upsert(c) == 0
        assert upsert(c, event_index=1) == 1  # second output to the same address
        assert upsert(c, address=OTHER_ADDRESS) == 1
        c.execute(t.delete().where(t.c.tx_hash == base["tx_hash"]))


def test_plain_duplicate_insert_is_refused(app_engine: Engine, household: CryptoHousehold) -> None:
    expect_check(
        app_engine,
        household,
        "crypto_tx",
        tx_values(household.household_id, household.wallets["shared"]),
    )


def test_same_key_cannot_be_watched_twice(app_engine: Engine, household: CryptoHousehold) -> None:
    fixed = "9" * 64
    with app_engine.connect() as c:
        trans = c.begin()
        set_ctx(c, household.household_id, wide=True)
        values = wallet_values(household.household_id, None, identity_hash=fixed)
        c.execute(table("crypto_wallet").insert().values(**values))
        with pytest.raises(IntegrityError):
            c.execute(table("crypto_wallet").insert().values(**values))
        trans.rollback()


# --------------------------------------------------------------------------- visibility


def test_private_wallet_and_everything_under_it_is_hidden_from_others(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    w = household.wallets["private"]
    for person, seen in ((household.alex, 1), (household.sam, 0)):
        assert count_as(app_engine, household, person, "crypto_wallet", id=w) == seen
        assert count_as(app_engine, household, person, "crypto_address", wallet_id=w) == seen
        assert count_as(app_engine, household, person, "crypto_tx", wallet_id=w) == seen
    e = household.exchanges["private"]
    assert count_as(app_engine, household, household.sam, "exchange_account", id=e) == 0
    assert count_as(app_engine, household, household.sam, "crypto_tx", exchange_account_id=e) == 0


def test_summary_only_shows_the_wallet_but_not_addresses_or_transactions(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    w = household.wallets["summary_only"]
    assert count_as(app_engine, household, household.sam, "crypto_wallet", id=w) == 1
    assert count_as(app_engine, household, household.sam, "crypto_address", wallet_id=w) == 0
    assert count_as(app_engine, household, household.sam, "crypto_tx", wallet_id=w) == 0
    e = household.exchanges["summary_only"]
    assert count_as(app_engine, household, household.sam, "exchange_account", id=e) == 1
    assert count_as(app_engine, household, household.sam, "crypto_tx", exchange_account_id=e) == 0


@pytest.mark.parametrize("kind", ["shared", "joint"])
def test_shared_and_joint_wallets_are_fully_visible(
    app_engine: Engine, household: CryptoHousehold, kind: str
) -> None:
    w, e = household.wallets[kind], household.exchanges[kind]
    assert count_as(app_engine, household, household.sam, "crypto_wallet", id=w) == 1
    assert count_as(app_engine, household, household.sam, "crypto_address", wallet_id=w) == 1
    assert count_as(app_engine, household, household.sam, "crypto_tx", wallet_id=w) == 1
    assert count_as(app_engine, household, household.sam, "crypto_tx", exchange_account_id=e) == 1


def test_a_viewer_cannot_edit_or_delete_someone_elses_wallet_or_its_rows(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    for kind in ("shared", "summary_only"):
        w = household.wallets[kind]
        with app_engine.begin() as c:
            set_ctx(c, household.household_id, household.sam)
            tw, tx = table("crypto_wallet"), table("crypto_tx")
            upd = c.execute(tw.update().where(tw.c.id == w).values(visibility="private"))
            assert upd.rowcount == 0
            assert c.execute(tw.delete().where(tw.c.id == w)).rowcount == 0
            assert c.execute(tx.delete().where(tx.c.wallet_id == w)).rowcount == 0
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert(
                c,
                "crypto_tx",
                **tx_values(
                    household.household_id,
                    household.wallets["shared"],
                    tx_hash="0x" + "22" * 32,
                ),
            )


def test_a_person_cannot_add_a_wallet_for_someone_else(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        with pytest.raises(DBAPIError, match="row-level security"):
            _insert(c, "crypto_wallet", **wallet_values(household.household_id, household.alex))
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        own = _insert(c, "crypto_wallet", **wallet_values(household.household_id, household.sam))
        c.execute(table("crypto_wallet").delete().where(table("crypto_wallet").c.id == own))


def test_joint_wallet_is_writable_by_anyone_in_the_household(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    w = household.wallets["joint"]
    tw = table("crypto_wallet")
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.sam)
        assert c.execute(tw.update().where(tw.c.id == w).values(label="Renamed")).rowcount == 1
        c.execute(tw.update().where(tw.c.id == w).values(label="Example wallet"))


def test_another_households_wallet_cannot_be_referenced(
    app_engine: Engine, owner_engine: Engine, household: CryptoHousehold
) -> None:
    """A guessed wallet id from another household is refused: RLS hides it, and the composite
    foreign key (which includes household_id) would refuse it even if it did not."""
    with app_engine.begin() as c:
        other = c.execute(text("SELECT create_household('Example Other')")).scalar_one()
        set_ctx(c, other, wide=True)
        foreign = _insert(c, "crypto_wallet", **wallet_values(other, None))
    try:
        with app_engine.connect() as c:
            trans = c.begin()
            set_ctx(c, household.household_id, wide=True)
            with pytest.raises((IntegrityError, DBAPIError)):
                c.execute(
                    table("crypto_tx").insert().values(**tx_values(household.household_id, foreign))
                )
            trans.rollback()
    finally:
        delete_household(owner_engine, other)


def test_prices_are_household_scoped_and_positive(
    app_engine: Engine, household: CryptoHousehold
) -> None:
    t = table("price_history")
    row = {
        "household_id": household.household_id,
        "asset": "ETH",
        "date": date(2026, 2, 1),
        "source": "example",
        "close_usd": Decimal("0.000000000001"),
    }
    with app_engine.begin() as c:
        set_ctx(c, household.household_id, household.alex)
        c.execute(t.insert().values(**row))
        got = c.execute(t.select().where(t.c.asset == "ETH")).mappings().one()
        assert got["close_usd"] == Decimal("0.000000000001")
        c.execute(t.delete().where(t.c.asset == "ETH"))
    expect_check(app_engine, household, "price_history", {**row, "close_usd": Decimal("0")})
