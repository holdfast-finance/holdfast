"""``/api/crypto/wallets*``: watch-only wallets and their transactions (ADR 0020).

Thin edge (ADR 0011): validate, call the service, shape the response. The one free-form input,
``key_or_address``, is public data only; the service refuses anything that looks like a seed phrase
or private key with a fixed message and never echoes the input. Wallet keys and descriptors are
never returned.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import Ctx, CurrentPrincipal
from app.schemas.crypto import TxPage, WalletCreate, WalletList, WalletOut
from app.services.crypto_wallets import PAGE_DEFAULT, PAGE_MAX, CryptoWalletService

router = APIRouter(tags=["crypto"])


@router.post("/crypto/wallets", status_code=status.HTTP_201_CREATED)
async def create_wallet(body: WalletCreate, ctx: Ctx, principal: CurrentPrincipal) -> WalletOut:
    return await CryptoWalletService(ctx, principal).create(body)


@router.get("/crypto/wallets")
async def list_wallets(ctx: Ctx, principal: CurrentPrincipal) -> WalletList:
    return await CryptoWalletService(ctx, principal).list()


@router.get("/crypto/wallets/{wallet_id}")
async def get_wallet(wallet_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal) -> WalletOut:
    return await CryptoWalletService(ctx, principal).get(wallet_id)


@router.delete("/crypto/wallets/{wallet_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wallet(wallet_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal) -> None:
    await CryptoWalletService(ctx, principal).delete(wallet_id)


@router.get("/crypto/wallets/{wallet_id}/transactions")
async def wallet_transactions(
    wallet_id: uuid.UUID,
    ctx: Ctx,
    principal: CurrentPrincipal,
    limit: int = Query(default=PAGE_DEFAULT, ge=1, le=PAGE_MAX),
    before: str | None = Query(default=None, max_length=128),
) -> TxPage:
    return await CryptoWalletService(ctx, principal).transactions(
        wallet_id, limit=limit, before=before
    )
