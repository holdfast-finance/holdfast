"""Crypto wallet schemas (ADR 0020). Quantities are strings on the wire (hard rule 1).

Nothing here can carry a seed phrase or private key: the one free-form input, ``key_or_address``, is
validated in the service by the secret-material guard and is never echoed, logged or returned. A
wallet's descriptor and xpub are encrypted at rest and are not part of any output schema.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from app.schemas.common import CryptoQuantityStr, Model, Name

Network = Literal["mainnet", "testnet", "signet", "regtest"]
ScriptType = Literal["p2pkh", "p2sh_p2wpkh", "p2wpkh", "p2tr"]
WalletStatus = Literal["active", "needs_review", "error"]
Visibility = Literal["private", "shared", "summary_only"]


class WalletCreate(Model):
    """A watch-only wallet: an xpub, an output descriptor or a single address. Public data only."""

    label: Name
    chain: Annotated[str, Field(min_length=1, max_length=32)]
    key_or_address: Annotated[str, Field(min_length=1, max_length=4096)]
    script_type: ScriptType | None = None
    network: Network | None = None
    owner: Literal["me", "joint"] = "me"
    visibility: Visibility = "shared"
    gap_limit: Annotated[int, Field(ge=1, le=200)] = 20


class WalletOut(Model):
    id: uuid.UUID
    label: str
    chain: str
    network: Network
    kind: Literal["watch_xpub", "watch_descriptor", "watch_address"]
    script_type: str | None
    fingerprint: str | None
    derivation_path: str | None
    gap_limit: int
    visibility: Visibility
    owner_person_id: uuid.UUID | None
    status: WalletStatus
    last_synced_at: datetime | None
    address_count: int
    # So the user can compare it with the hardware wallet's screen (PLAN 18.2 onboarding).
    first_receive_address: str | None
    created_at: datetime


class WalletList(Model):
    items: list[WalletOut]


class TxOut(Model):
    id: uuid.UUID
    wallet_id: uuid.UUID
    chain: str
    tx_hash: str
    address: str
    event_index: int
    direction: Literal["in", "out"]
    asset: str
    asset_contract: str
    quantity: CryptoQuantityStr
    fee_asset: str | None
    fee_qty: CryptoQuantityStr | None
    block_height: int | None
    block_time: datetime | None
    counterparty: str | None
    label: str | None
    classification: str | None


class TxPage(Model):
    items: list[TxOut]
    next_cursor: str | None


class SyncOut(Model):
    """What a sync did. Counts only: no addresses, keys or amounts."""

    outcome: Literal["reconciled", "needs_review", "history_unavailable", "failed"]
    inserted: int
    updated: int
    rejected: int
    addresses: int
