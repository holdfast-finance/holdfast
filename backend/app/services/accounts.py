"""Change an account's owner and visibility (A1).

No migration. Before C4c, an owner change is refused while any row on the account
has a transfer group. Dissolving pairs belongs to C4c and stays out of this file.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session
from app.models import Account, Document, ImportRun, Transaction
from app.schemas.accounts import AccountOwnershipIn
from app.schemas.ledger import AccountOut
from app.services.audit import add as audit_add
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.sessions import Principal

OwnerKind = Literal["me", "joint", "other"]
# Stronger than a key-share lock, and it still does not block an insert's FK check.
ACCOUNT_LOCK = "FOR NO KEY UPDATE"
_UNFINISHED = ("pending", "needs_review")
_IMPORT_OPEN = "An import on this account isn't finished yet."


def owner_kind(owner_person_id: uuid.UUID | None, person_id: uuid.UUID | None) -> OwnerKind:
    """How ``person_id`` sees this owner. Null owner is joint."""
    if owner_person_id is None:
        return "joint"
    if person_id is not None and owner_person_id == person_id:
        return "me"
    return "other"


def can_edit_account(owner_person_id: uuid.UUID | None, person_id: uuid.UUID | None) -> bool:
    """Writable when the account is joint or this person owns it. Never from visibility."""
    if person_id is None:
        return False
    return owner_person_id is None or owner_person_id == person_id


def can_change_owner(
    owner_person_id: uuid.UUID | None, person_id: uuid.UUID | None, *, is_owner: bool
) -> bool:
    """True when this reader can make a change the service would accept."""
    if person_id is None:
        return False
    if owner_person_id == person_id:
        return True
    # A joint account can be claimed only by the household owner.
    return owner_person_id is None and is_owner


class AccountService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    async def change_ownership(self, account_id: uuid.UUID, body: AccountOwnershipIn) -> AccountOut:
        async with scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        ) as s:
            account = await self._lock(s, account_id)
            if account is None or not can_edit_account(
                account.owner_person_id, self.principal.person_id
            ):
                raise ApiError("not_found", "That account wasn't found.")
            new_owner, new_visibility = self._next(account, body)
            owner_changes = new_owner != account.owner_person_id
            # The database lets any member claim a joint account (ADR 0008). This
            # check lives in the service only. There is no second claim path.
            claiming = owner_changes and account.owner_person_id is None
            if claiming and not self.principal.is_owner:
                raise ApiError("forbidden", "Only the household owner can claim a joint account.")
            if owner_changes and await self._has_pairs(s, account.id):
                raise ApiError("conflict", "This account has paired transfers.")
            if claiming and await self._other_import_open(s, account.id):
                raise ApiError("conflict", _IMPORT_OPEN)
            same = not owner_changes and new_visibility == account.visibility
            if same:
                return await self._out(s, account.id)
            before_kind = owner_kind(account.owner_person_id, self.principal.person_id)
            before_visibility = account.visibility
            account.owner_person_id = new_owner
            account.visibility = new_visibility
            try:
                await s.flush()
            except DBAPIError as exc:
                # The ownership trigger's text names the rule. Callers get the same
                # 404 as a missing account. Any other database error stays an error.
                if getattr(exc.orig, "sqlstate", None) != "42501":
                    raise
                raise ApiError("not_found", "That account wasn't found.") from None
            after_kind = owner_kind(account.owner_person_id, self.principal.person_id)
            audit_add(
                s,
                "account.ownership_changed",
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
                entity="account",
                entity_id=account.id,
                detail={
                    "account_id": str(account.id),
                    "owner_kind_before": before_kind,
                    "owner_kind_after": after_kind,
                    "visibility_before": before_visibility,
                    "visibility_after": account.visibility,
                },
            )
            return await self._out(s, account.id)

    async def _lock(self, s: AsyncSession, account_id: uuid.UUID) -> Account | None:
        stmt = select(Account).where(Account.id == account_id).suffix_with(ACCOUNT_LOCK)
        return (await s.execute(stmt)).scalar_one_or_none()

    def _next(self, account: Account, body: AccountOwnershipIn) -> tuple[uuid.UUID | None, str]:
        if body.owner is None:
            stays_joint = account.owner_person_id is None
        else:
            stays_joint = body.owner == "joint"
        if body.visibility is not None and stays_joint:
            raise ApiError("validation_error", "A joint account has no visibility of its own.")
        new_owner = account.owner_person_id
        new_visibility = account.visibility
        if body.owner == "joint":
            new_owner = None
        elif body.owner == "me":
            new_owner = self.principal.person_id
            if account.owner_person_id is None and body.visibility is None:
                new_visibility = "shared"
        if body.visibility is not None:
            new_visibility = body.visibility
        return new_owner, new_visibility

    async def _has_pairs(self, s: AsyncSession, account_id: uuid.UUID) -> bool:
        stmt = (
            select(Transaction.id)
            .where(
                Transaction.account_id == account_id,
                Transaction.transfer_group_id.is_not(None),
            )
            .limit(1)
        )
        return (await s.execute(stmt)).first() is not None

    async def _other_import_open(self, s: AsyncSession, account_id: uuid.UUID) -> bool:
        me = self.principal.person_id
        stmt = (
            select(ImportRun.id)
            .join(Document, Document.id == ImportRun.document_id)
            .where(
                ImportRun.account_id == account_id,
                ImportRun.reconcile_status.in_(_UNFINISHED),
                Document.person_id.is_not(None),
                Document.person_id != me,
            )
            .limit(1)
        )
        return (await s.execute(stmt)).first() is not None

    async def _out(self, s: AsyncSession, account_id: uuid.UUID) -> AccountOut:
        # Imported here so this module can load without the ledger service importing us first.
        from app.services.ledger import load_account_out

        found = await load_account_out(s, self.principal, account_id)
        if found is None:
            raise ApiError("not_found", "That account wasn't found.")
        return found
