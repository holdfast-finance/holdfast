"""Crypto Track, watch-only (ADR 0020): wallets, addresses, transactions, exchange accounts, prices.

Tables come from autogenerate (reviewed by hand). Written below because autogenerate cannot see it:

- ENABLE + FORCE RLS and the ``household_isolation`` policy on all five tables.
- RESTRICTIVE per-person policies (ADR 0008). ``crypto_wallet`` and ``exchange_account`` copy the
  ``account`` predicates (read: shared / summary_only / joint / mine / household-wide; write: joint,
  mine or household-wide). ``crypto_address`` and ``crypto_tx`` inherit through EXISTS on their
  wallet or exchange account and are hidden from ``summary_only`` viewers, like ``transaction``.
  ``price_history`` is household-scoped market data with no per-person rule.

No private-key column exists anywhere (hard rule 11): ``crypto_wallet.is_watch_only`` is CHECKed
true and ``exchange_account.permissions`` can never say trade or withdraw. Lots and disposals are
Phase 10b and are not part of this migration. Grants come from the owner's default privileges.

Revision ID: 0007_crypto_track
Revises: 0006_account_import_unique
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_crypto_track"
down_revision: str | None = "0006_account_import_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = ("crypto_wallet", "crypto_address", "crypto_tx", "exchange_account", "price_history")

# Rows of a wallet / exchange account the viewer may see or write (same shape as ``account``).
VISIBLE = (
    "visibility IN ('shared', 'summary_only') OR owner_person_id IS NULL"
    " OR owner_person_id = app_person_id() OR app_household_wide()"
)
WRITABLE = "owner_person_id IS NULL OR owner_person_id = app_person_id() OR app_household_wide()"
# Details of a parent: not summary_only for someone else. The subquery is itself filtered by the
# parent's RLS.
DETAIL_VISIBLE = (
    "EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {table}.{col} AND (p.visibility = 'shared'"
    " OR p.owner_person_id IS NULL OR p.owner_person_id = app_person_id()"
    " OR app_household_wide()))"
)
DETAIL_WRITABLE = (
    "EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {table}.{col}"
    " AND (p.owner_person_id IS NULL OR p.owner_person_id = app_person_id()))"
)


def _restrictive(table: str, command: str, using: str | None, check: str | None) -> None:
    name = {"SELECT": "person_read", "INSERT": "person_insert"}.get(
        command, f"person_{command.lower()}"
    )
    sql = f'CREATE POLICY {name} ON "{table}" AS RESTRICTIVE FOR {command}'
    if using is not None:
        sql += f" USING ({using})"
    if check is not None:
        sql += f" WITH CHECK ({check})"
    op.execute(sql)


def _person_policies(table: str, read: str, write: str) -> None:
    _restrictive(table, "SELECT", read, None)
    _restrictive(table, "INSERT", None, write)
    _restrictive(table, "UPDATE", write, write)
    _restrictive(table, "DELETE", write, None)


def _row_security() -> None:
    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY household_isolation ON "{table}" FOR ALL'
            " USING (household_id = app_household_id())"
            " WITH CHECK (household_id = app_household_id())"
        )
    _person_policies("crypto_wallet", VISIBLE, WRITABLE)
    _person_policies("exchange_account", VISIBLE, WRITABLE)

    def detail(parent: str, table: str, col: str, writable: bool) -> str:
        return (DETAIL_WRITABLE if writable else DETAIL_VISIBLE).format(
            parent=parent, table=table, col=col
        )

    def either(table: str, writable: bool) -> str:
        wallet = detail("crypto_wallet", table, "wallet_id", writable)
        exchange = detail("exchange_account", table, "exchange_account_id", writable)
        pred = (
            f"(wallet_id IS NOT NULL AND {wallet})"
            f" OR (exchange_account_id IS NOT NULL AND {exchange})"
        )
        return f"app_household_wide() OR {pred}" if writable else pred

    address_write = "app_household_wide() OR " + detail(
        "crypto_wallet", "crypto_address", "wallet_id", True
    )
    _person_policies(
        "crypto_address",
        detail("crypto_wallet", "crypto_address", "wallet_id", False),
        address_write,
    )
    _person_policies("crypto_tx", either("crypto_tx", False), either("crypto_tx", True))


def upgrade() -> None:
    op.create_table(
        "price_history",
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("asset", sa.Text(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("close_usd", sa.Numeric(precision=28, scale=12), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'", name=op.f("ck_price_history_asset_format")
        ),
        sa.CheckConstraint(
            "source ~ '^[a-z][a-z0-9_]{1,63}$'", name=op.f("ck_price_history_source_format")
        ),
        sa.CheckConstraint("close_usd > 0", name=op.f("ck_price_history_close_positive")),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_price_history_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "household_id", "asset", "date", "source", name=op.f("pk_price_history")
        ),
    )
    op.create_table(
        "crypto_wallet",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("owner_person_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("chain", sa.Text(), nullable=False),
        sa.Column("network", sa.Text(), server_default=sa.text("'mainnet'"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("descriptor_enc", sa.LargeBinary(), nullable=True),
        sa.Column("identity_hash", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=True),
        sa.Column("derivation_path", sa.Text(), nullable=True),
        sa.Column("script_type", sa.Text(), nullable=True),
        sa.Column("gap_limit", sa.Integer(), server_default=sa.text("20"), nullable=False),
        sa.Column("is_watch_only", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'shared'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(kind = 'watch_address') = (descriptor_enc IS NULL)",
            name=op.f("ck_crypto_wallet_key_matches_kind"),
        ),
        sa.CheckConstraint(
            "chain ~ '^[a-z][a-z0-9_]{1,63}$'", name=op.f("ck_crypto_wallet_chain_format")
        ),
        sa.CheckConstraint(
            "derivation_path IS NULL OR derivation_path ~ '^m(/[0-9]{1,10}h?)*$'",
            name=op.f("ck_crypto_wallet_derivation_path_format"),
        ),
        sa.CheckConstraint(
            "fingerprint IS NULL OR fingerprint ~ '^[0-9a-f]{8}$'",
            name=op.f("ck_crypto_wallet_fingerprint_format"),
        ),
        sa.CheckConstraint(
            "identity_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_crypto_wallet_identity_hash_format")
        ),
        sa.CheckConstraint(
            "kind <> 'watch_address' OR (fingerprint IS NULL AND derivation_path IS NULL "
            "AND script_type IS NULL)",
            name=op.f("ck_crypto_wallet_address_has_no_key_metadata"),
        ),
        sa.CheckConstraint(
            "kind = 'watch_address' OR chain = 'bitcoin'",
            name=op.f("ck_crypto_wallet_xpub_is_bitcoin"),
        ),
        sa.CheckConstraint(
            "kind IN ('watch_xpub', 'watch_descriptor', 'watch_address')",
            name=op.f("ck_crypto_wallet_kind"),
        ),
        sa.CheckConstraint(
            "network IN ('mainnet', 'testnet', 'signet', 'regtest')",
            name=op.f("ck_crypto_wallet_network"),
        ),
        sa.CheckConstraint(
            "script_type IS NULL OR script_type IN ('p2pkh', 'p2sh_p2wpkh', 'p2wpkh', "
            "'p2tr', 'p2wsh')",
            name=op.f("ck_crypto_wallet_script_type"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'needs_review', 'error')", name=op.f("ck_crypto_wallet_status")
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'shared', 'summary_only')",
            name=op.f("ck_crypto_wallet_visibility"),
        ),
        sa.CheckConstraint(
            "gap_limit BETWEEN 1 AND 200", name=op.f("ck_crypto_wallet_gap_limit_range")
        ),
        sa.CheckConstraint("is_watch_only", name=op.f("ck_crypto_wallet_watch_only")),
        sa.CheckConstraint(
            "length(label) BETWEEN 1 AND 200", name=op.f("ck_crypto_wallet_label_length")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_crypto_wallet_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_crypto_wallet_owner_person_id_person"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crypto_wallet")),
        sa.UniqueConstraint(
            "household_id",
            "chain",
            "network",
            "identity_hash",
            name=op.f("uq_crypto_wallet_household_id_chain_network_identity_hash"),
        ),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_crypto_wallet_id_household_id")),
    )
    op.create_index(
        op.f("ix_crypto_wallet_household_id"), "crypto_wallet", ["household_id"], unique=False
    )
    op.create_index(
        op.f("ix_crypto_wallet_owner_person_id"), "crypto_wallet", ["owner_person_id"], unique=False
    )
    op.create_table(
        "exchange_account",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("owner_person_id", sa.Uuid(), nullable=True),
        sa.Column("exchange_id", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("api_key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("api_secret_enc", sa.LargeBinary(), nullable=False),
        sa.Column("passphrase_enc", sa.LargeBinary(), nullable=True),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visibility", sa.Text(), server_default=sa.text("'shared'"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "exchange_id ~ '^[a-z][a-z0-9_]{1,63}$'",
            name=op.f("ck_exchange_account_exchange_id_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(permissions) = 'object' AND coalesce(permissions->>'trade', "
            "'false') = 'false' AND coalesce(permissions->>'withdraw', 'false') = 'false'",
            name=op.f("ck_exchange_account_permissions_read_only"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'needs_attention', 'error', 'disconnected')",
            name=op.f("ck_exchange_account_status"),
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'shared', 'summary_only')",
            name=op.f("ck_exchange_account_visibility"),
        ),
        sa.CheckConstraint(
            "length(label) BETWEEN 1 AND 200", name=op.f("ck_exchange_account_label_length")
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_exchange_account_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_person_id", "household_id"],
            ["person.id", "person.household_id"],
            name=op.f("fk_exchange_account_owner_person_id_person"),
            ondelete="NO ACTION",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exchange_account")),
        sa.UniqueConstraint(
            "household_id",
            "exchange_id",
            "label",
            name=op.f("uq_exchange_account_household_id_exchange_id_label"),
        ),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_exchange_account_id_household_id")),
    )
    op.create_index(
        op.f("ix_exchange_account_household_id"), "exchange_account", ["household_id"], unique=False
    )
    op.create_index(
        op.f("ix_exchange_account_owner_person_id"),
        "exchange_account",
        ["owner_person_id"],
        unique=False,
    )
    op.create_table(
        "crypto_address",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=True),
        sa.Column("address_index", sa.Integer(), nullable=True),
        sa.Column("is_change", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(address) BETWEEN 10 AND 120 AND address !~ '\\s'",
            name=op.f("ck_crypto_address_address_shape"),
        ),
        sa.CheckConstraint(
            "path IS NULL OR path ~ '^(m/)?[0-9]{1,10}h?(/[0-9]{1,10}h?)*$'",
            name=op.f("ck_crypto_address_path_format"),
        ),
        sa.CheckConstraint(
            "address_index IS NULL OR address_index >= 0",
            name=op.f("ck_crypto_address_address_index_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_crypto_address_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id", "household_id"],
            ["crypto_wallet.id", "crypto_wallet.household_id"],
            name=op.f("fk_crypto_address_wallet_id_crypto_wallet"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crypto_address")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_crypto_address_id_household_id")),
        sa.UniqueConstraint(
            "wallet_id", "address", name=op.f("uq_crypto_address_wallet_id_address")
        ),
    )
    op.create_index(
        op.f("ix_crypto_address_household_id"), "crypto_address", ["household_id"], unique=False
    )
    op.create_index(
        "ix_crypto_address_household_id_address",
        "crypto_address",
        ["household_id", "address"],
        unique=False,
    )
    op.create_index(
        op.f("ix_crypto_address_wallet_id"), "crypto_address", ["wallet_id"], unique=False
    )
    op.create_table(
        "crypto_tx",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("wallet_id", sa.Uuid(), nullable=True),
        sa.Column("exchange_account_id", sa.Uuid(), nullable=True),
        sa.Column("chain", sa.Text(), nullable=True),
        sa.Column("tx_hash", sa.Text(), nullable=True),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("event_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("block_height", sa.Integer(), nullable=True),
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("asset", sa.Text(), nullable=False),
        sa.Column("asset_contract", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("fee_asset", sa.Text(), nullable=True),
        sa.Column("fee_qty", sa.Numeric(precision=38, scale=18), nullable=True),
        sa.Column("counterparty", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=True),
        sa.Column("transfer_group_id", sa.Uuid(), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(direction = 'in' AND quantity >= 0) OR (direction = 'out' AND quantity <= 0)",
            name=op.f("ck_crypto_tx_quantity_sign_matches_direction"),
        ),
        sa.CheckConstraint(
            "asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'", name=op.f("ck_crypto_tx_asset_format")
        ),
        sa.CheckConstraint(
            "asset_contract = '' OR asset_contract ~ '^0x[0-9a-f]{40}$'",
            name=op.f("ck_crypto_tx_asset_contract_format"),
        ),
        sa.CheckConstraint(
            "chain IS NULL OR chain ~ '^[a-z][a-z0-9_]{1,63}$'",
            name=op.f("ck_crypto_tx_chain_format"),
        ),
        sa.CheckConstraint(
            "classification IS NULL OR classification IN ('buy', 'sell', 'swap', "
            "'transfer_in', 'transfer_out', 'income_staking', 'income_mining', 'airdrop', "
            "'gift', 'spend', 'fee')",
            name=op.f("ck_crypto_tx_classification"),
        ),
        sa.CheckConstraint("direction IN ('in', 'out')", name=op.f("ck_crypto_tx_direction")),
        sa.CheckConstraint(
            "fee_asset IS NULL OR fee_asset ~ '^[A-Z0-9][A-Z0-9._-]{0,31}$'",
            name=op.f("ck_crypto_tx_fee_asset_format"),
        ),
        sa.CheckConstraint(
            "raw IS NULL OR jsonb_typeof(raw) = 'object'", name=op.f("ck_crypto_tx_raw_is_object")
        ),
        sa.CheckConstraint(
            "tx_hash IS NULL OR tx_hash ~ '^(0x)?[0-9a-f]{64}$'",
            name=op.f("ck_crypto_tx_tx_hash_format"),
        ),
        sa.CheckConstraint(
            "(fee_asset IS NULL) = (fee_qty IS NULL)", name=op.f("ck_crypto_tx_fee_pair")
        ),
        sa.CheckConstraint(
            "(wallet_id IS NULL) <> (exchange_account_id IS NULL)",
            name=op.f("ck_crypto_tx_exactly_one_parent"),
        ),
        sa.CheckConstraint("event_index >= 0", name=op.f("ck_crypto_tx_event_index_non_negative")),
        sa.CheckConstraint(
            "exchange_account_id IS NULL OR external_id IS NOT NULL",
            name=op.f("ck_crypto_tx_exchange_row_has_external_id"),
        ),
        sa.CheckConstraint(
            "fee_qty IS NULL OR fee_qty >= 0", name=op.f("ck_crypto_tx_fee_qty_non_negative")
        ),
        sa.CheckConstraint(
            "wallet_id IS NULL OR (chain IS NOT NULL AND tx_hash IS NOT NULL AND address IS "
            "NOT NULL)",
            name=op.f("ck_crypto_tx_wallet_row_has_chain_hash_address"),
        ),
        sa.ForeignKeyConstraint(
            ["exchange_account_id", "household_id"],
            ["exchange_account.id", "exchange_account.household_id"],
            name=op.f("fk_crypto_tx_exchange_account_id_exchange_account"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["household_id"],
            ["household.id"],
            name=op.f("fk_crypto_tx_household_id_household"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["wallet_id", "household_id"],
            ["crypto_wallet.id", "crypto_wallet.household_id"],
            name=op.f("fk_crypto_tx_wallet_id_crypto_wallet"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crypto_tx")),
        sa.UniqueConstraint("id", "household_id", name=op.f("uq_crypto_tx_id_household_id")),
    )
    op.create_index(
        op.f("ix_crypto_tx_exchange_account_id"), "crypto_tx", ["exchange_account_id"], unique=False
    )
    op.create_index(op.f("ix_crypto_tx_household_id"), "crypto_tx", ["household_id"], unique=False)
    op.create_index(
        "ix_crypto_tx_household_id_block_time",
        "crypto_tx",
        ["household_id", "block_time"],
        unique=False,
    )
    op.create_index(op.f("ix_crypto_tx_wallet_id"), "crypto_tx", ["wallet_id"], unique=False)
    op.create_index(
        "uq_crypto_tx_exchange_event",
        "crypto_tx",
        ["exchange_account_id", "external_id", "direction", "asset"],
        unique=True,
        postgresql_where=sa.text("exchange_account_id IS NOT NULL"),
    )
    op.create_index(
        "uq_crypto_tx_wallet_event",
        "crypto_tx",
        [
            "wallet_id",
            "chain",
            "tx_hash",
            "address",
            "direction",
            "asset",
            "asset_contract",
            "event_index",
        ],
        unique=True,
        postgresql_where=sa.text("wallet_id IS NOT NULL"),
    )
    _row_security()


def downgrade() -> None:
    # Dropping a table drops its policies. Children first (autogenerate order).
    op.drop_index(
        "uq_crypto_tx_wallet_event",
        table_name="crypto_tx",
        postgresql_where=sa.text("wallet_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_crypto_tx_exchange_event",
        table_name="crypto_tx",
        postgresql_where=sa.text("exchange_account_id IS NOT NULL"),
    )
    op.drop_index(op.f("ix_crypto_tx_wallet_id"), table_name="crypto_tx")
    op.drop_index("ix_crypto_tx_household_id_block_time", table_name="crypto_tx")
    op.drop_index(op.f("ix_crypto_tx_household_id"), table_name="crypto_tx")
    op.drop_index(op.f("ix_crypto_tx_exchange_account_id"), table_name="crypto_tx")
    op.drop_table("crypto_tx")
    op.drop_index(op.f("ix_crypto_address_wallet_id"), table_name="crypto_address")
    op.drop_index("ix_crypto_address_household_id_address", table_name="crypto_address")
    op.drop_index(op.f("ix_crypto_address_household_id"), table_name="crypto_address")
    op.drop_table("crypto_address")
    op.drop_index(op.f("ix_exchange_account_owner_person_id"), table_name="exchange_account")
    op.drop_index(op.f("ix_exchange_account_household_id"), table_name="exchange_account")
    op.drop_table("exchange_account")
    op.drop_index(op.f("ix_crypto_wallet_owner_person_id"), table_name="crypto_wallet")
    op.drop_index(op.f("ix_crypto_wallet_household_id"), table_name="crypto_wallet")
    op.drop_table("crypto_wallet")
    op.drop_table("price_history")
