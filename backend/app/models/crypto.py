"""Crypto Track, watch-only (ADR 0020, PLAN 18.5): wallets, addresses, transactions, exchange
accounts and daily price closes.

**Public data only (hard rule 11).** A wallet holds an xpub, a descriptor or a single address.
There is no column for a seed phrase or any private key, and ``is_watch_only`` is CHECKed true
until a
Phase 10c migration deliberately relaxes it. An xpub or descriptor reveals a wallet's full history,
so
it is stored Fernet-encrypted (``descriptor_enc``); ``identity_hash`` (SHA-256 of the canonical
text)
gives per-household uniqueness without decrypting. Exchange credentials are encrypted the same way,
and ``permissions`` can never record trade or withdraw rights (CHECK).

**Visibility** follows ``account`` (ADR 0008): ``crypto_wallet`` and ``exchange_account`` carry
``owner_person_id`` (NULL = joint) and ``visibility``. ``crypto_address`` and ``crypto_tx`` inherit
through their wallet or exchange account; a ``summary_only`` viewer sees the wallet row, not its
addresses or transactions.

**Quantities** are ``NUMERIC(38,18)`` and signed (inflow positive, outflow negative; ``direction``
is a CHECKed redundancy). Fees are non-negative magnitudes. Never float.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    PrimaryKeyConstraint,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models._common import (
    ACCOUNT_VISIBILITIES,
    CRYPTO_ASSET_PATTERN,
    CRYPTO_NETWORKS,
    CRYPTO_SCRIPT_TYPES,
    CRYPTO_SLUG_PATTERN,
    CRYPTO_TX_CLASSIFICATIONS,
    CRYPTO_TX_DIRECTIONS,
    CRYPTO_TX_HASH_PATTERN,
    CRYPTO_WALLET_KINDS,
    CRYPTO_WALLET_STATUSES,
    EXCHANGE_ACCOUNT_STATUSES,
    SHA256_HEX_PATTERN,
    UUIDPK,
    CreatedAt,
    CryptoPrice,
    CryptoQuantity,
    HouseholdId,
    check_in,
    check_non_negative,
    child_fk,
    parent_key,
)

# A lower-case EVM contract address, or '' for the chain's native asset.
CONTRACT_SQL = "asset_contract = '' OR asset_contract ~ '^0x[0-9a-f]{40}$'"


class CryptoWallet(Base):
    """A watch-only wallet: an xpub, an output descriptor or one address on one chain.

    ``descriptor_enc`` is the canonical xpub or descriptor, Fernet-encrypted, and is NULL exactly
    for
    ``watch_address``. Only Bitcoin has xpubs and descriptors. ``fingerprint`` (master key
    fingerprint, 8 hex), ``derivation_path`` (``m/84h/0h/0h``) and ``script_type`` describe the key
    and are not secret. ``gap_limit`` is how many unused addresses past the last used one we derive.
    ``status`` becomes ``needs_review`` when a sync's sum of transactions does not equal the chain's
    reported balance (hard rule 6, ADR 0020).
    """

    __tablename__ = "crypto_wallet"
    __table_args__ = (
        check_in("kind", CRYPTO_WALLET_KINDS),
        check_in("network", CRYPTO_NETWORKS),
        check_in("script_type", CRYPTO_SCRIPT_TYPES, nullable=True),
        check_in("visibility", ACCOUNT_VISIBILITIES),
        check_in("status", CRYPTO_WALLET_STATUSES),
        child_fk("owner_person_id", "person", ondelete="NO ACTION"),
        CheckConstraint("length(label) BETWEEN 1 AND 200", name="label_length"),
        CheckConstraint(f"chain ~ '{CRYPTO_SLUG_PATTERN}'", name="chain_format"),
        CheckConstraint(f"identity_hash ~ '{SHA256_HEX_PATTERN}'", name="identity_hash_format"),
        CheckConstraint(
            "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{8}$'", name="fingerprint_format"
        ),
        CheckConstraint(
            "derivation_path IS NULL OR derivation_path ~ '^m(/[0-9]{1,10}h?)*$'",
            name="derivation_path_format",
        ),
        CheckConstraint("gap_limit BETWEEN 1 AND 200", name="gap_limit_range"),
        CheckConstraint("is_watch_only", name="watch_only"),
        CheckConstraint(
            "(kind = 'watch_address') = (descriptor_enc IS NULL)", name="key_matches_kind"
        ),
        CheckConstraint("kind = 'watch_address' OR chain = 'bitcoin'", name="xpub_is_bitcoin"),
        CheckConstraint(
            "kind <> 'watch_address' OR (fingerprint IS NULL AND derivation_path IS NULL"
            " AND script_type IS NULL)",
            name="address_has_no_key_metadata",
        ),
        UniqueConstraint("household_id", "chain", "network", "identity_hash"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    # NULL = joint (ADR 0008). NO ACTION: reassign before deleting a person.
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    label: Mapped[str]
    chain: Mapped[str]
    network: Mapped[str] = mapped_column(server_default=text("'mainnet'"))
    kind: Mapped[str]
    descriptor_enc: Mapped[bytes | None]
    identity_hash: Mapped[str]
    fingerprint: Mapped[str | None]
    derivation_path: Mapped[str | None]
    script_type: Mapped[str | None]
    gap_limit: Mapped[int] = mapped_column(server_default=text("20"))
    is_watch_only: Mapped[bool] = mapped_column(server_default=text("true"))
    visibility: Mapped[str] = mapped_column(server_default=text("'shared'"))
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    last_synced_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class CryptoAddress(Base):
    """An address of a wallet (public data). ``watch_address`` wallets have exactly one.

    ``path`` is the derivation path when known (``m/84h/0h/0h/0/5`` or relative ``0/5``);
    ``address_index`` and ``is_change`` are the last two path steps, kept for gap-limit queries.
    """

    __tablename__ = "crypto_address"
    __table_args__ = (
        child_fk("wallet_id", "crypto_wallet"),
        CheckConstraint(
            "length(address) BETWEEN 10 AND 120 AND address !~ '\\s'", name="address_shape"
        ),
        CheckConstraint(
            "path IS NULL OR path ~ '^(m/)?[0-9]{1,10}h?(/[0-9]{1,10}h?)*$'", name="path_format"
        ),
        check_non_negative("address_index"),
        UniqueConstraint("wallet_id", "address"),
        Index("ix_crypto_address_household_id_address", "household_id", "address"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    wallet_id: Mapped[uuid.UUID] = mapped_column(index=True)
    address: Mapped[str]
    path: Mapped[str | None]
    address_index: Mapped[int | None]
    is_change: Mapped[bool] = mapped_column(server_default=text("false"))
    first_seen: Mapped[datetime | None]
    label: Mapped[str | None]
    created_at: Mapped[CreatedAt]


class ExchangeAccount(Base):
    """A read-only exchange API key (PLAN 18.0 item 5). Keys are Fernet ciphertext. Never log or
    return them.

    ``permissions`` records what the exchange reported for the key, for example
    ``{"read": true, "trade": false, "withdraw": false, "reported": true}``. ``reported`` false
    means the
    exchange cannot say, and the service shows a blocking warning. A CHECK refuses ``trade`` or
    ``withdraw`` true, so a trade-capable key cannot be stored as safe.
    """

    __tablename__ = "exchange_account"
    __table_args__ = (
        check_in("visibility", ACCOUNT_VISIBILITIES),
        check_in("status", EXCHANGE_ACCOUNT_STATUSES),
        child_fk("owner_person_id", "person", ondelete="NO ACTION"),
        CheckConstraint("length(label) BETWEEN 1 AND 200", name="label_length"),
        CheckConstraint(f"exchange_id ~ '{CRYPTO_SLUG_PATTERN}'", name="exchange_id_format"),
        CheckConstraint(
            "jsonb_typeof(permissions) = 'object'"
            " AND coalesce(permissions->>'trade', 'false') = 'false'"
            " AND coalesce(permissions->>'withdraw', 'false') = 'false'",
            name="permissions_read_only",
        ),
        UniqueConstraint("household_id", "exchange_id", "label"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    owner_person_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    exchange_id: Mapped[str]
    label: Mapped[str]
    api_key_enc: Mapped[bytes]
    api_secret_enc: Mapped[bytes]
    passphrase_enc: Mapped[bytes | None]
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB)
    visibility: Mapped[str] = mapped_column(server_default=text("'shared'"))
    status: Mapped[str] = mapped_column(server_default=text("'active'"))
    last_synced_at: Mapped[datetime | None]
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class CryptoTx(Base):
    """One asset movement of a wallet address or an exchange account.

    Exactly one of ``wallet_id`` / ``exchange_account_id`` is set. A wallet row is keyed
    ``(wallet_id, chain, tx_hash, address, direction, asset, asset_contract, event_index)``, so a
    sync
    that sees the same movement twice changes nothing; ``event_index`` (output or log index) and
    the asset
    are part of the key because one transaction can pay one address twice or move two tokens. An
    exchange
    row is keyed ``(exchange_account_id, external_id, direction, asset)``.

    ``quantity`` is signed: inflow positive, outflow negative (hard rule 1), zero allowed for a
    fee-only
    transaction. ``fee_qty`` is a non-negative magnitude in ``fee_asset``. ``block_time`` NULL means
    unconfirmed. ``asset_contract`` is the lower-case token contract, or '' for the native asset.
    ``transfer_group_id`` pairs an exchange withdrawal with its wallet deposit (transfer matching).
    ``raw`` is the source's public record, never a credential.
    """

    __tablename__ = "crypto_tx"
    __table_args__ = (
        check_in("direction", CRYPTO_TX_DIRECTIONS),
        check_in("classification", CRYPTO_TX_CLASSIFICATIONS, nullable=True),
        child_fk("wallet_id", "crypto_wallet"),
        child_fk("exchange_account_id", "exchange_account"),
        CheckConstraint(
            "(wallet_id IS NULL) <> (exchange_account_id IS NULL)", name="exactly_one_parent"
        ),
        CheckConstraint(
            "wallet_id IS NULL OR (chain IS NOT NULL AND tx_hash IS NOT NULL"
            " AND address IS NOT NULL)",
            name="wallet_row_has_chain_hash_address",
        ),
        CheckConstraint(
            "exchange_account_id IS NULL OR external_id IS NOT NULL",
            name="exchange_row_has_external_id",
        ),
        CheckConstraint(f"chain IS NULL OR chain ~ '{CRYPTO_SLUG_PATTERN}'", name="chain_format"),
        CheckConstraint(
            f"tx_hash IS NULL OR tx_hash ~ '{CRYPTO_TX_HASH_PATTERN}'", name="tx_hash_format"
        ),
        CheckConstraint(f"asset ~ '{CRYPTO_ASSET_PATTERN}'", name="asset_format"),
        CheckConstraint(CONTRACT_SQL, name="asset_contract_format"),
        CheckConstraint(
            f"fee_asset IS NULL OR fee_asset ~ '{CRYPTO_ASSET_PATTERN}'", name="fee_asset_format"
        ),
        CheckConstraint("event_index >= 0", name="event_index_non_negative"),
        CheckConstraint(
            "(direction = 'in' AND quantity >= 0) OR (direction = 'out' AND quantity <= 0)",
            name="quantity_sign_matches_direction",
        ),
        CheckConstraint("(fee_asset IS NULL) = (fee_qty IS NULL)", name="fee_pair"),
        check_non_negative("fee_qty"),
        CheckConstraint("raw IS NULL OR jsonb_typeof(raw) = 'object'", name="raw_is_object"),
        Index(
            "uq_crypto_tx_wallet_event",
            "wallet_id",
            "chain",
            "tx_hash",
            "address",
            "direction",
            "asset",
            "asset_contract",
            "event_index",
            unique=True,
            postgresql_where=text("wallet_id IS NOT NULL"),
        ),
        Index(
            "uq_crypto_tx_exchange_event",
            "exchange_account_id",
            "external_id",
            "direction",
            "asset",
            unique=True,
            postgresql_where=text("exchange_account_id IS NOT NULL"),
        ),
        Index("ix_crypto_tx_household_id_block_time", "household_id", "block_time"),
        parent_key(),
    )

    id: Mapped[UUIDPK]
    household_id: Mapped[HouseholdId]
    wallet_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    exchange_account_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    chain: Mapped[str | None]
    tx_hash: Mapped[str | None]
    external_id: Mapped[str | None]
    address: Mapped[str | None]
    event_index: Mapped[int] = mapped_column(server_default=text("0"))
    block_height: Mapped[int | None]
    block_time: Mapped[datetime | None]
    direction: Mapped[str]
    asset: Mapped[str]
    asset_contract: Mapped[str] = mapped_column(server_default=text("''"))
    quantity: Mapped[CryptoQuantity]
    fee_asset: Mapped[str | None]
    fee_qty: Mapped[CryptoQuantity | None]
    counterparty: Mapped[str | None]
    label: Mapped[str | None]
    classification: Mapped[str | None]
    transfer_group_id: Mapped[uuid.UUID | None]
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PriceHistory(Base):
    """A daily USD close for an asset from one source (PLAN 18.3, ADR 0020).

    Household-scoped, not global: a shared table would let one household write the closes another
    household's cost basis rests on. ``source`` is recorded for auditability.
    """

    __tablename__ = "price_history"
    __table_args__ = (
        PrimaryKeyConstraint("household_id", "asset", "date", "source"),
        CheckConstraint(f"asset ~ '{CRYPTO_ASSET_PATTERN}'", name="asset_format"),
        CheckConstraint(f"source ~ '{CRYPTO_SLUG_PATTERN}'", name="source_format"),
        CheckConstraint("close_usd > 0", name="close_positive"),
    )

    # Same FK as HouseholdId, spelled out because it is part of the primary key.
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"))
    asset: Mapped[str]
    date: Mapped[date]
    source: Mapped[str]
    close_usd: Mapped[CryptoPrice]
    created_at: Mapped[CreatedAt]


__all__ = [
    "CryptoAddress",
    "CryptoTx",
    "CryptoWallet",
    "ExchangeAccount",
    "PriceHistory",
]
