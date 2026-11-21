"""Watch-only crypto wallets: create, sync, list, delete (ADR 0020, ADR 0011).

**Public data only (hard rule 11).** The one free-form input is an xpub, descriptor or address. It
goes through the secret-material guard first, is encrypted before it touches the database, and is
never logged, audited, echoed or returned. Audit entries carry ids, kinds and counts, never a key,
descriptor or address list.

**Sync** (``sync_wallet``) asks a :class:`ChainSource` for the wallet's history, upserts
``crypto_tx`` idempotently on its natural key, and reconciles (hard rule 6): per asset, the sum of
stored quantities minus the fees paid in that asset must equal the balance the source reports,
exactly. A wallet that does not reconcile is ``needs_review``, never silently accepted. Quantities
are ``Decimal`` end to end; a value that does not fit ``NUMERIC(38,18)`` is rejected, not rounded.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    and_,
    cast,
    delete,
    func,
    literal,
    literal_column,
    or_,
    select,
    text,
    tuple_,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db_context import scoped_session
from app.domain.crypto import bitcoin
from app.domain.crypto.chains import ChainKind, get_chain
from app.domain.crypto.errors import CryptoInputError
from app.domain.crypto.evm import address_key, normalise_evm_address
from app.domain.crypto.guard import reject_secret_material
from app.domain.crypto.units import fits_quantity
from app.integrations.crypto.chains.base import ChainSource, ChainSourceError, HistoryUnavailable
from app.models import CryptoAddress, CryptoTx, CryptoWallet
from app.schemas.crypto import SyncOut, TxOut, TxPage, WalletCreate, WalletList, WalletOut
from app.security import crypto as fernet
from app.services import audit
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.sessions import Principal

MAX_DISCOVERY_ROUNDS = 20
PAGE_DEFAULT = 50
PAGE_MAX = 200
_DESCRIPTOR_START = re.compile(r"^(?:wpkh|sh|pkh|tr|wsh)\(")
_KEY_START = re.compile(r"^(?:\[|[xyztuv]pub)")
_TX_HASH = re.compile(r"(?:0x)?[0-9a-f]{64}")
_HEX_INFINITY = cast(literal("infinity"), DateTime(timezone=True))


def _seal(value: str) -> bytes:
    """Fernet-encrypt a public key or descriptor (it reveals the wallet's full history).

    One place to switch to the ``hf1`` envelope when ADR 0015's build lands.
    """
    return fernet.encrypt(value).encode("ascii")


def _open(value: bytes) -> str:
    return fernet.decrypt(value.decode("ascii"))


def _invalid(error: CryptoInputError) -> ApiError:
    # The message is fixed text that never echoes the input.
    return ApiError("validation_error", error.message)


# --------------------------------------------------------------------------- creating


@dataclass(frozen=True)
class _NewWallet:
    kind: str
    network: str
    identity_hash: str
    descriptor: str | None
    script_type: str | None
    fingerprint: str | None
    derivation_path: str | None
    addresses: tuple[bitcoin.DerivedAddress | str, ...]
    chain: str


def _prepare(body: WalletCreate) -> _NewWallet:
    """Validate public input and work out what to store. Raises ``ApiError`` (fixed text)."""
    try:
        reject_secret_material(body.label)
        chain = get_chain(body.chain)
        text_in = body.key_or_address.strip()
        if chain.kind is ChainKind.EVM:
            address = normalise_evm_address(text_in)
            return _NewWallet(
                "watch_address",
                "mainnet",
                hashlib.sha256(address_key(address).encode("ascii")).hexdigest(),
                None,
                None,
                None,
                None,
                (address,),
                chain.slug,
            )
        network = bitcoin.Network(body.network) if body.network else None
        if _DESCRIPTOR_START.match(text_in) or _KEY_START.match(text_in):
            script = bitcoin.ScriptType(body.script_type) if body.script_type else None
            key = bitcoin.parse_watch_key(text_in, script_type=script, network=network)
            kind = "watch_descriptor" if _DESCRIPTOR_START.match(text_in) else "watch_xpub"
            first = bitcoin.derive_addresses(key, change=False, start=0, count=body.gap_limit)
            change = bitcoin.derive_addresses(key, change=True, start=0, count=body.gap_limit)
            return _NewWallet(
                kind,
                key.network.value,
                key.identity_hash,
                key.descriptor,
                key.script_type.value,
                key.fingerprint,
                key.derivation_path,
                (*first, *change),
                chain.slug,
            )
        net = network or bitcoin.Network.MAINNET
        address = bitcoin.normalise_bitcoin_address(text_in, net)
        return _NewWallet(
            "watch_address",
            net.value,
            hashlib.sha256(address.encode("ascii")).hexdigest(),
            None,
            None,
            None,
            None,
            (address,),
            chain.slug,
        )
    except CryptoInputError as error:
        raise _invalid(error) from None


class CryptoWalletService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    def _session(self) -> Any:
        return scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        )

    async def create(self, body: WalletCreate) -> WalletOut:
        new = _prepare(body)
        owner: uuid.UUID | None = None
        if body.owner == "me":
            if self.principal.person_id is None:
                raise ApiError(
                    "validation_error", "Pick a person for this wallet, or make it joint."
                )
            owner = self.principal.person_id
        async with self._session() as s:
            wallet = CryptoWallet(
                household_id=self.principal.household_id,
                owner_person_id=owner,
                label=" ".join(body.label.split()),
                chain=new.chain,
                network=new.network,
                kind=new.kind,
                descriptor_enc=_seal(new.descriptor) if new.descriptor else None,
                identity_hash=new.identity_hash,
                fingerprint=new.fingerprint,
                derivation_path=new.derivation_path,
                script_type=new.script_type,
                gap_limit=body.gap_limit,
                visibility=body.visibility,
            )
            try:
                async with s.begin_nested():
                    s.add(wallet)
                    await s.flush()
            except IntegrityError:
                raise ApiError("conflict", "That wallet is already being tracked.") from None
            rows = [self._address_row(wallet, a) for a in new.addresses]
            await s.execute(
                pg_insert(CryptoAddress)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["wallet_id", "address"])
            )
            audit.add(
                s,
                "crypto.wallet.created",
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
                entity="crypto_wallet",
                entity_id=wallet.id,
                detail={"kind": new.kind, "chain": new.chain, "addresses": len(rows)},
            )
            return await self._out(s, wallet)

    @staticmethod
    def _address_row(wallet: CryptoWallet, a: bitcoin.DerivedAddress | str) -> dict[str, Any]:
        base = {"household_id": wallet.household_id, "wallet_id": wallet.id}
        if isinstance(a, str):
            return {**base, "address": a}
        return {
            **base,
            "address": a.address,
            "path": a.path,
            "address_index": a.index,
            "is_change": a.is_change,
        }

    # ------------------------------------------------------------------ reading

    async def _out(self, s: AsyncSession, w: CryptoWallet) -> WalletOut:
        count = (
            await s.execute(
                select(func.count())
                .select_from(CryptoAddress)
                .where(CryptoAddress.wallet_id == w.id)
            )
        ).scalar_one()
        first = (
            await s.execute(
                select(CryptoAddress.address)
                .where(CryptoAddress.wallet_id == w.id, CryptoAddress.is_change.is_(False))
                .order_by(CryptoAddress.address_index.asc().nulls_last(), CryptoAddress.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        return WalletOut.model_validate(
            {
                "id": w.id,
                "label": w.label,
                "chain": w.chain,
                "network": w.network,
                "kind": w.kind,
                "script_type": w.script_type,
                "fingerprint": w.fingerprint,
                "derivation_path": w.derivation_path,
                "gap_limit": w.gap_limit,
                "visibility": w.visibility,
                "owner_person_id": w.owner_person_id,
                "status": w.status,
                "last_synced_at": w.last_synced_at,
                "address_count": count,
                "first_receive_address": first,
                "created_at": w.created_at,
            }
        )

    async def list(self) -> WalletList:
        async with self._session() as s:
            wallets = (
                (
                    await s.execute(
                        select(CryptoWallet).order_by(CryptoWallet.created_at, CryptoWallet.id)
                    )
                )
                .scalars()
                .all()
            )
            return WalletList(items=[await self._out(s, w) for w in wallets])

    async def get(self, wallet_id: uuid.UUID) -> WalletOut:
        async with self._session() as s:
            return await self._out(s, await _wallet(s, wallet_id))

    async def delete(self, wallet_id: uuid.UUID) -> None:
        async with self._session() as s:
            wallet = await _wallet(s, wallet_id)
            # Addresses and transactions cascade. A shared wallet someone else owns is visible but
            # not writable (ADR 0008), so RLS matches no row: say so instead of pretending.
            deleted = await s.execute(delete(CryptoWallet).where(CryptoWallet.id == wallet_id))
            if deleted.rowcount == 0:
                raise ApiError("forbidden", "Only the wallet's owner can remove it.")
            audit.add(
                s,
                "crypto.wallet.deleted",
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
                entity="crypto_wallet",
                entity_id=wallet_id,
                detail={"kind": wallet.kind, "chain": wallet.chain},
            )

    async def transactions(
        self, wallet_id: uuid.UUID, *, limit: int = PAGE_DEFAULT, before: str | None = None
    ) -> TxPage:
        limit = max(1, min(limit, PAGE_MAX))
        async with self._session() as s:
            await _wallet(s, wallet_id)
            order = func.coalesce(CryptoTx.block_time, _HEX_INFINITY)
            stmt = select(CryptoTx).where(CryptoTx.wallet_id == wallet_id)
            if before is not None:
                stamp, row_id = _parse_cursor(before)
                stmt = stmt.where(tuple_(order, CryptoTx.id) < tuple_(stamp, row_id))
            rows = (
                (await s.execute(stmt.order_by(order.desc(), CryptoTx.id.desc()).limit(limit + 1)))
                .scalars()
                .all()
            )
            page = rows[:limit]
            more = rows[-2] if len(rows) > limit else None
            cursor = _cursor(more) if more is not None else None
            return TxPage(items=[_tx_out(t) for t in page], next_cursor=cursor)


async def _wallet(s: AsyncSession, wallet_id: uuid.UUID) -> CryptoWallet:
    wallet = (
        await s.execute(select(CryptoWallet).where(CryptoWallet.id == wallet_id))
    ).scalar_one_or_none()
    if wallet is None:  # also what a wallet hidden by RLS looks like
        raise ApiError("not_found", "That wallet was not found.")
    return wallet


def _tx_out(t: CryptoTx) -> TxOut:
    return TxOut.model_validate(
        {
            "id": t.id,
            "wallet_id": t.wallet_id,
            "chain": t.chain,
            "tx_hash": t.tx_hash,
            "address": t.address,
            "event_index": t.event_index,
            "direction": t.direction,
            "asset": t.asset,
            "asset_contract": t.asset_contract,
            "quantity": t.quantity,
            "fee_asset": t.fee_asset,
            "fee_qty": t.fee_qty,
            "block_height": t.block_height,
            "block_time": t.block_time,
            "counterparty": t.counterparty,
            "label": t.label,
            "classification": t.classification,
        }
    )


def _cursor(t: CryptoTx) -> str:
    stamp = "infinity" if t.block_time is None else t.block_time.isoformat()
    return f"{stamp}|{t.id}"


def _parse_cursor(value: str) -> tuple[datetime | str, uuid.UUID]:
    stamp, _, raw_id = value.partition("|")
    try:
        row_id = uuid.UUID(raw_id)
        parsed: datetime | str = (
            "infinity" if stamp == "infinity" else datetime.fromisoformat(stamp)
        )
    except ValueError:
        raise ApiError("validation_error", "That page cursor is not valid.") from None
    return parsed, row_id


# --------------------------------------------------------------------------- syncing


@dataclass(frozen=True)
class _Mine:
    """The sync-relevant facts of a wallet, read once."""

    wallet_id: uuid.UUID
    household_id: uuid.UUID
    gap_limit: int
    descriptor: str | None
    chain: str
    addresses: dict[str, tuple[bool, int | None]]  # address -> (is_change, index)


async def sync_wallet(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    person_id: uuid.UUID | None,
    wallet_id: uuid.UUID,
    source: ChainSource,
    *,
    user_id: uuid.UUID | None = None,
    household_wide: bool = False,
) -> SyncOut:
    """Sync one wallet through ``source`` (a ``ChainSource``). Idempotent: a second run with no new
    chain data inserts nothing and changes nothing."""

    async def finish(outcome: str, status: str, counts: tuple[int, int, int, int]) -> SyncOut:
        async with scoped_session(
            factory, household_id, person_id, household_wide=household_wide
        ) as s:
            wallet = await _wallet(s, wallet_id)
            wallet.status = status
            if outcome in ("reconciled", "needs_review"):
                wallet.last_synced_at = datetime.now(UTC)
            audit.add(
                s,
                "crypto.wallet.synced",
                household_id=household_id,
                user_id=user_id,
                outcome="success" if outcome in ("reconciled", "needs_review") else "failure",
                entity="crypto_wallet",
                entity_id=wallet_id,
                detail={"result": outcome, "inserted": counts[0], "updated": counts[1]},
            )
        return SyncOut.model_validate(
            {
                "outcome": outcome,
                "inserted": counts[0],
                "updated": counts[1],
                "rejected": counts[2],
                "addresses": counts[3],
            }
        )

    async with scoped_session(factory, household_id, person_id, household_wide=household_wide) as s:
        mine = await _load(s, wallet_id)
    try:
        key = bitcoin.parse_watch_key(mine.descriptor) if mine.descriptor else None
        transfers, addresses = await _collect(
            factory, household_id, person_id, household_wide, mine, key, source
        )
    except HistoryUnavailable:
        return await finish("history_unavailable", "needs_review", (0, 0, 0, len(mine.addresses)))
    except ChainSourceError:
        return await finish("failed", "error", (0, 0, 0, len(mine.addresses)))

    async with scoped_session(factory, household_id, person_id, household_wide=household_wide) as s:
        inserted, updated, rejected = await _upsert(s, mine, transfers, addresses)
    try:
        balances = await source.balances(list(addresses))
    except ChainSourceError:
        return await finish("failed", "error", (inserted, updated, rejected, len(addresses)))
    async with scoped_session(factory, household_id, person_id, household_wide=household_wide) as s:
        ok = rejected == 0 and await _reconciles(s, wallet_id, balances)
    outcome, status = ("reconciled", "active") if ok else ("needs_review", "needs_review")
    return await finish(outcome, status, (inserted, updated, rejected, len(addresses)))


async def _load(s: AsyncSession, wallet_id: uuid.UUID) -> _Mine:
    wallet = await _wallet(s, wallet_id)
    rows = (
        await s.execute(
            select(
                CryptoAddress.address, CryptoAddress.is_change, CryptoAddress.address_index
            ).where(CryptoAddress.wallet_id == wallet_id)
        )
    ).all()
    descriptor = _open(wallet.descriptor_enc) if wallet.descriptor_enc else None
    return _Mine(
        wallet_id=wallet.id,
        household_id=wallet.household_id,
        gap_limit=wallet.gap_limit,
        descriptor=descriptor,
        chain=wallet.chain,
        addresses={a: (bool(c), i) for a, c, i in rows},
    )


async def _collect(
    factory: async_sessionmaker[AsyncSession],
    household_id: uuid.UUID,
    person_id: uuid.UUID | None,
    household_wide: bool,
    mine: _Mine,
    key: bitcoin.WatchKey | None,
    source: ChainSource,
) -> tuple[list[Any], dict[str, tuple[bool, int | None]]]:
    """History for every address, extending a descriptor wallet until the gap limit is satisfied."""
    addresses = dict(mine.addresses)
    if key is None:
        return list(await source.history(list(addresses))), addresses
    counts = {
        branch: max(
            _branch_count(addresses, branch),
            mine.gap_limit,
        )
        for branch in (False, True)
    }
    transfers: list[Any] = []
    for _ in range(MAX_DISCOVERY_ROUNDS):
        fresh: list[bitcoin.DerivedAddress] = []
        for branch, count in counts.items():
            have = _branch_count(addresses, branch)
            if count > have:
                fresh += bitcoin.derive_addresses(
                    key, change=branch, start=have, count=count - have
                )
        if fresh:
            async with scoped_session(
                factory, household_id, person_id, household_wide=household_wide
            ) as s:
                await s.execute(
                    pg_insert(CryptoAddress)
                    .values(
                        [
                            {
                                "household_id": mine.household_id,
                                "wallet_id": mine.wallet_id,
                                "address": a.address,
                                "path": a.path,
                                "address_index": a.index,
                                "is_change": a.is_change,
                            }
                            for a in fresh
                        ]
                    )
                    .on_conflict_do_nothing(index_elements=["wallet_id", "address"])
                )
            for a in fresh:
                addresses[a.address] = (a.is_change, a.index)
        transfers = list(await source.history(list(addresses)))
        needed = dict(counts)
        for branch in (False, True):
            used = [
                addresses[t.address][1]
                for t in transfers
                if t.address in addresses and addresses[t.address][0] is branch
            ]
            last = max((i for i in used if i is not None), default=None)
            needed[branch] = bitcoin.addresses_needed(last, mine.gap_limit)
        if all(needed[b] <= counts[b] for b in counts):
            break
        counts = {b: max(counts[b], needed[b]) for b in counts}
    return transfers, addresses


def _branch_count(addresses: dict[str, tuple[bool, int | None]], branch: bool) -> int:
    indexes = [i for is_change, i in addresses.values() if is_change is branch and i is not None]
    return max(indexes) + 1 if indexes else 0


def _acceptable(t: Any, known: dict[str, tuple[bool, int | None]]) -> bool:
    """Defence in depth: a source may not hand us rows for addresses we did not ask about, malformed
    hashes, wrong signs or values the column cannot hold exactly."""
    if t.address not in known or _TX_HASH.fullmatch(t.tx_hash) is None:
        return False
    if t.direction == "in" and t.quantity < 0:
        return False
    if t.direction == "out" and t.quantity > 0:
        return False
    if not fits_quantity(t.quantity):
        return False
    return t.fee_qty is None or (t.fee_qty >= 0 and fits_quantity(t.fee_qty))


async def _upsert(
    s: AsyncSession,
    mine: _Mine,
    transfers: Sequence[Any],
    addresses: dict[str, tuple[bool, int | None]],
) -> tuple[int, int, int]:
    good = [t for t in transfers if _acceptable(t, addresses)]
    inserted = updated = 0
    for t in good:
        row = {
            "household_id": mine.household_id,
            "wallet_id": mine.wallet_id,
            "chain": mine.chain,
            "tx_hash": t.tx_hash,
            "address": t.address,
            "event_index": t.event_index,
            "direction": t.direction,
            "asset": t.asset,
            "asset_contract": t.asset_contract,
            "quantity": t.quantity,
            "fee_asset": t.fee_asset,
            "fee_qty": t.fee_qty,
            "block_height": t.block_height,
            "block_time": t.block_time,
            "counterparty": t.counterparty,
        }
        insert = pg_insert(CryptoTx).values(**row)
        changeable = ("block_height", "block_time", "fee_asset", "fee_qty", "counterparty")
        upsert: Any = insert.on_conflict_do_update(
            index_elements=[
                "wallet_id",
                "chain",
                "tx_hash",
                "address",
                "direction",
                "asset",
                "asset_contract",
                "event_index",
            ],
            index_where=text("wallet_id IS NOT NULL"),
            # Only chain-derived columns move (pending -> confirmed); label and classification are
            # the user's. No-change conflicts write nothing, so a re-sync leaves updated_at alone.
            set_={c: getattr(insert.excluded, c) for c in changeable},
            where=or_(
                *(
                    getattr(CryptoTx, c).is_distinct_from(getattr(insert.excluded, c))
                    for c in changeable
                )
            ),
        ).returning(literal_column("(xmax = 0)"))
        result = (await s.execute(upsert)).scalar_one_or_none()
        if result is None:
            continue
        if result:
            inserted += 1
        else:
            updated += 1
    # first_seen: the earliest confirmed movement of each address.
    firsts: dict[str, datetime] = {}
    for t in good:
        if t.block_time is not None and (
            t.address not in firsts or t.block_time < firsts[t.address]
        ):
            firsts[t.address] = t.block_time
    for address, when in firsts.items():
        await s.execute(
            text(
                "UPDATE crypto_address SET first_seen = :when"
                " WHERE wallet_id = :w AND address = :a AND first_seen IS NULL"
            ),
            {"when": when, "w": mine.wallet_id, "a": address},
        )
    return inserted, updated, len(transfers) - len(good)


async def _reconciles(s: AsyncSession, wallet_id: uuid.UUID, balances: Sequence[Any]) -> bool:
    """Per asset: sum(quantity) - sum(fees paid in that asset) == the source's reported balance."""
    stored: dict[tuple[str, str], Decimal] = {}
    rows = (
        await s.execute(
            select(CryptoTx.asset, CryptoTx.asset_contract, func.sum(CryptoTx.quantity))
            .where(CryptoTx.wallet_id == wallet_id)
            .group_by(CryptoTx.asset, CryptoTx.asset_contract)
        )
    ).all()
    for asset, contract, total in rows:
        stored[(asset, contract)] = Decimal(total)
    fees = (
        await s.execute(
            select(CryptoTx.fee_asset, func.sum(CryptoTx.fee_qty))
            .where(and_(CryptoTx.wallet_id == wallet_id, CryptoTx.fee_asset.is_not(None)))
            .group_by(CryptoTx.fee_asset)
        )
    ).all()
    for fee_asset, fee_total in fees:
        if fee_asset is None or fee_total is None:
            continue
        stored[(fee_asset, "")] = stored.get((fee_asset, ""), Decimal(0)) - Decimal(fee_total)
    reported: dict[tuple[str, str], Decimal] = {}
    for b in balances:
        key = (b.asset, b.asset_contract)
        reported[key] = reported.get(key, Decimal(0)) + b.quantity
    keys = set(stored) | set(reported)
    return all(stored.get(k, Decimal(0)) == reported.get(k, Decimal(0)) for k in keys)
