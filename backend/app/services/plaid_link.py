"""Plaid Link: the link plan, the slot-guarded Link token, the idempotent exchange, and
resolving or abandoning a reservation (ADR 0034; PHASE-2 contract 5.1 to 5.3).

This is the one module besides the setup probe that may call ``link_token_create`` (pinned by
``tests/test_plaid_link_token_callers.py``). A token made here leaves the service, so every
production new-Item token is behind the slot guard first, and no Plaid call happens before the
guard has passed.

Secrets: the Link token is sealed ``hf2`` into ``plaid_link_slot.link_token_enc`` while the
reservation is open and is NULL from the moment it leaves ``reserved``. The access token is
plaintext only between the exchange response and its sealing, which is committed before
``/accounts/get``. A ``public_token`` is never stored. No Plaid response body is logged, and
audit rows carry ids and codes only.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session
from app.integrations.plaid.client import (
    PlaidClient,
    PlaidCredentials,
    PlaidError,
    PlaidUnavailable,
    credentials_well_formed,
)
from app.integrations.plaid.link import (
    ABANDON_PHRASE,
    COUNT_SOURCE,
    SLOT_LIMIT_ERROR_CODES,
    SLOT_TOTAL,
    DaysNotAllowedError,
    LinkProducts,
    ProductNotAllowedError,
    link_products,
    parse_link_sessions,
    slot_phrase,
)
from app.models import BankConnection, ConnectorAccountLink, PlaidLinkSlot
from app.schemas.connections import (
    AbandonIn,
    ExchangeOut,
    ExistingConnectionOut,
    LinkPlanIn,
    LinkPlanOut,
    LinkTokenIn,
    LinkTokenOut,
    SlotOut,
)
from app.security import crypto
from app.security.secret_token import SecretToken
from app.services import audit, plaid_setup
from app.services.auth import require_fresh
from app.services.connections import connection_by_id
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.household import require_owner
from app.services.plaid_slots import (
    assert_can_link,
    client_fp,
    lock_household,
    slot_count,
    sweep_stale,
)
from app.services.sessions import Principal

CLIENT_NAME = "Holdfast"
SECOND_PUBLIC_TOKEN = "second_public_token"  # noqa: S105  (a flag code, not a secret)
_ACCOUNT_ID_MAX = 128


def _external_id_ok(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= _ACCOUNT_ID_MAX
        and value.isascii()
        and value.isprintable()
        and " " not in value
    )


def _plaid_api_error(exc: PlaidError | PlaidUnavailable) -> ApiError:
    """Codes only: Plaid's own message text is never shown."""
    if isinstance(exc, PlaidUnavailable):
        return ApiError("plaid_unavailable", "Plaid could not be reached. Try again shortly.")
    if exc.error_code in SLOT_LIMIT_ERROR_CODES:
        return ApiError("no_slots_left", "Plaid says this client has no Item slots left.")
    return ApiError("plaid_error", f"Plaid refused the request ({exc.error_code}).")


def _slot_out(slot: PlaidLinkSlot) -> SlotOut:
    return SlotOut.model_validate(
        {
            "reservation_id": slot.id,
            "state": slot.state,
            "flag": slot.flag,
            "connection_id": slot.bank_connection_id,
        }
    )


class PlaidLinkService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    # ------------------------------------------------------------------ helpers

    def _session(self) -> contextlib.AbstractAsyncContextManager[AsyncSession]:
        return scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        )

    def _credentials(self) -> tuple[PlaidCredentials, str]:
        creds = plaid_setup.plaid_credentials(self.ctx.settings)
        if creds is None or not credentials_well_formed(creds):
            raise ApiError("conflict", "Plaid keys aren't set up for this environment yet.")
        return creds, client_fp(creds.client_id.reveal())

    def _products(self, login_kind: str) -> LinkProducts:
        settings = self.ctx.settings
        try:
            return link_products(
                login_kind, settings.plaid_products, settings.plaid_tx_days_requested
            )
        except DaysNotAllowedError:
            raise ApiError(
                "days_not_allowed", "Holdfast asks Plaid for 730 days of history, no other."
            ) from None
        except ProductNotAllowedError:
            raise ApiError(
                "product_not_allowed",
                "PLAID_PRODUCTS holds a product Link may not request, or lacks the one this "
                "login needs. Allowed: transactions, liabilities, investments.",
            ) from None

    def _key(self) -> Any:
        return self.ctx.settings.fernet_key

    @staticmethod
    def _slot_ctx(household_id: uuid.UUID, slot_id: uuid.UUID) -> str:
        return crypto.make_context(household_id, "plaid_link_slot", "link_token_enc", str(slot_id))

    @staticmethod
    def _conn_ctx(household_id: uuid.UUID, conn_id: uuid.UUID) -> str:
        return crypto.make_context(
            household_id, "bank_connection", "access_token_enc", str(conn_id)
        )

    def _audit(
        self,
        s: AsyncSession,
        action: str,
        slot_id: uuid.UUID | None,
        detail: dict[str, Any],
        outcome: audit.Outcome = "success",
    ) -> None:
        audit.add(
            s,
            action,
            household_id=self.principal.household_id,
            user_id=self.principal.user_id,
            ip=self.ctx.ip,
            outcome=outcome,
            entity="plaid_link_slot" if slot_id else None,
            entity_id=slot_id,
            detail=detail,
        )

    async def _audit_failure(
        self, action: str, slot_id: uuid.UUID | None, detail: dict[str, Any]
    ) -> None:
        await audit.record(
            self.ctx.factory,
            action,
            household_id=self.principal.household_id,
            user_id=self.principal.user_id,
            ip=self.ctx.ip,
            outcome="failure",
            entity="plaid_link_slot" if slot_id else None,
            entity_id=slot_id,
            detail=detail,
        )

    # ------------------------------------------------------------------ link plan

    async def link_plan(self, body: LinkPlanIn) -> LinkPlanOut:
        """A dry call: no Plaid request, no reservation."""
        require_owner(self.principal)
        lp = self._products(body.login_kind)
        _creds, fp = self._credentials()
        env = self.ctx.settings.plaid_env
        async with self._session() as s:
            await lock_household(s, self.principal.household_id)
            await sweep_stale(s, self.principal.household_id)
            ledger = await slot_count(s, fp, "production")
            conns = (
                (await s.execute(select(BankConnection).order_by(BankConnection.created_at)))
                .scalars()
                .all()
            )
            existing = [
                ExistingConnectionOut.model_validate(
                    {
                        "id": c.id,
                        "provider": c.provider,
                        "env": c.env,
                        "status": c.status,
                        "created_at": c.created_at,
                    }
                )
                for c in conns
            ]
        phrase = (
            slot_phrase(ledger)
            if env == "production" and body.mode == "new" and ledger < 10
            else None
        )
        return LinkPlanOut(
            products=list(lp.products),
            optional_products=list(lp.optional_products),
            days_requested=lp.days_requested,
            ledger_count=ledger,
            count_source=COUNT_SOURCE,
            existing_connections=existing,
            phrase=phrase,
        )

    # ------------------------------------------------------------------ link token

    async def link_token(self, body: LinkTokenIn) -> LinkTokenOut:
        require_owner(self.principal)
        require_fresh(self.principal)
        lp = self._products(body.login_kind)
        creds, fp = self._credentials()
        if body.mode == "update":
            return await self._update_token(body, creds, fp)
        env = self.ctx.settings.plaid_env
        hid = self.principal.household_id
        async with self._session() as s:
            await lock_household(s, hid)
            await sweep_stale(s, hid)
            await assert_can_link(s, env)
            owner_count: int | None = None
            slot_n: int | None = None
            if env == "production":
                ledger = await slot_count(s, fp, "production")
                if ledger >= SLOT_TOTAL:
                    raise ApiError("no_slots_left", "All 10 Plaid Item slots are used.")
                if body.owner_count is None or not body.not_a_duplicate:
                    raise ApiError(
                        "confirm_required",
                        "Enter your own Item count and confirm this isn't a duplicate login.",
                    )
                n = max(ledger, body.owner_count)
                if n >= SLOT_TOTAL:
                    raise ApiError("no_slots_left", "All 10 Plaid Item slots are used.")
                if body.confirm_phrase != slot_phrase(n):
                    raise ApiError("confirm_required", "Type the slot phrase exactly to continue.")
                owner_count, slot_n = body.owner_count, n + 1
            slot = PlaidLinkSlot(
                id=uuid.uuid4(),
                household_id=hid,
                client_fp=fp,
                env=env,
                state="reserved",
                owner_count=owner_count,
                created_by_user_id=self.principal.user_id,
            )
            s.add(slot)
            try:
                await s.flush()
            except IntegrityError:
                raise ApiError(
                    "busy", "Another Link for this Plaid client is still open. Resolve it first."
                ) from None
            # The Plaid call runs inside this transaction: a failure or crash leaves no
            # reservation behind, so no slot is spent on a token that never existed.
            try:
                async with plaid_setup.open_client(creds) as plaid:
                    data = await self._create_token(plaid, body, lp)
            except (PlaidError, PlaidUnavailable) as exc:
                await self._audit_failure(
                    "plaid.link_token",
                    None,
                    {"env": env, "mode": "new", "error_code": getattr(exc, "error_code", None)},
                )
                raise _plaid_api_error(exc) from None
            token, expiration = data.get("link_token"), data.get("expiration")
            if not isinstance(token, str) or not token:
                raise ApiError("plaid_unavailable", "Plaid sent something unexpected.")
            slot.link_token_enc = crypto.seal(
                SecretToken(token), self._slot_ctx(hid, slot.id), key=self._key()
            )
            self._audit(
                s, "plaid.link_token", slot.id, {"env": env, "mode": "new", "slot_n": slot_n}
            )
            reservation_id = slot.id
        return LinkTokenOut(
            link_token=token,
            expiration=expiration if isinstance(expiration, str) else None,
            reservation_id=reservation_id,
        )

    async def _create_token(
        self, plaid: PlaidClient, body: LinkTokenIn, lp: LinkProducts
    ) -> dict[str, Any]:
        settings = self.ctx.settings

        async def create(optional: list[str] | None) -> dict[str, Any]:
            return await plaid.link_token_create(
                client_user_id=str(self.principal.user_id),
                client_name=CLIENT_NAME,
                products=list(lp.products),
                country_codes=list(settings.plaid_country_codes),
                redirect_uri=settings.plaid_redirect_uri or None,
                transactions_days_requested=lp.days_requested,
                optional_products=optional,
            )

        if not lp.optional_products:
            return await create(None)
        try:
            return await create(list(lp.optional_products))
        except PlaidError as exc:
            # VERIFY on Trial: if optional_products is refused, ask for transactions only and
            # let P6 add liabilities by re-consent without a new Item.
            if exc.http_status != 400 or exc.error_type not in {"INVALID_REQUEST", "INVALID_INPUT"}:
                raise
            return await create(None)

    async def _update_token(
        self, body: LinkTokenIn, creds: PlaidCredentials, fp: str
    ) -> LinkTokenOut:
        """Relink an existing Item: the same Item, no reservation, no slot."""
        if body.connection_id is None:
            raise ApiError("validation_error", "Choose the connection to relink.")
        env = self.ctx.settings.plaid_env
        hid = self.principal.household_id
        async with self._session() as s:
            conn = await s.get(BankConnection, body.connection_id)
            if conn is None or conn.provider != "plaid":
                raise ApiError("not_found", "That connection doesn't exist.")
            if conn.env != env or conn.client_fp != fp:
                # Items belong to the Plaid client that made them (ADR 0034).
                raise ApiError(
                    "conflict", "This connection belongs to another Plaid client or environment."
                )
            try:
                access = crypto.open(
                    conn.access_token_enc, self._conn_ctx(hid, conn.id), key=self._key()
                )
            except crypto.CryptoError:
                raise ApiError(
                    "conflict", "That connection's saved credential can't be read."
                ) from None
            try:
                async with plaid_setup.open_client(creds) as plaid:
                    data = await plaid.link_token_create(
                        client_user_id=str(self.principal.user_id),
                        client_name=CLIENT_NAME,
                        products=[],
                        country_codes=list(self.ctx.settings.plaid_country_codes),
                        redirect_uri=self.ctx.settings.plaid_redirect_uri or None,
                        access_token=access,
                    )
            except (PlaidError, PlaidUnavailable) as exc:
                await self._audit_failure(
                    "plaid.link_token",
                    None,
                    {"env": env, "mode": "update", "connection_id": str(conn.id)},
                )
                raise _plaid_api_error(exc) from None
            token, expiration = data.get("link_token"), data.get("expiration")
            if not isinstance(token, str) or not token:
                raise ApiError("plaid_unavailable", "Plaid sent something unexpected.")
            self._audit(
                s,
                "plaid.link_token",
                None,
                {"env": env, "mode": "update", "connection_id": str(conn.id), "slot_n": None},
            )
        return LinkTokenOut(
            link_token=token,
            expiration=expiration if isinstance(expiration, str) else None,
            reservation_id=None,
        )

    # ------------------------------------------------------------------ exchange

    async def exchange(self, public_token: SecretToken, reservation_id: uuid.UUID) -> ExchangeOut:
        require_owner(self.principal)
        conn_id = await self._record_item(reservation_id, public_token, creator_only=True)
        async with self._session() as s:
            out = await connection_by_id(s, conn_id)
        if out is None:
            raise ApiError("not_found", "That connection doesn't exist.")
        return ExchangeOut(connection=out)

    async def _record_item(
        self, reservation_id: uuid.UUID, public_token: SecretToken, *, creator_only: bool
    ) -> uuid.UUID:
        """Exchange ``public_token`` for the reservation and record the Item. Returns the
        connection id. Safe to repeat: the same Item gives the same connection."""
        settings = self.ctx.settings
        hid = self.principal.household_id
        creds, fp = self._credentials()

        # 1. Check the reservation and the coexistence rule before Plaid is asked for anything,
        #    so a refusal here can't orphan an Item.
        async with self._session() as s:
            await lock_household(s, hid)
            slot = await s.get(PlaidLinkSlot, reservation_id)
            if slot is None or (creator_only and slot.created_by_user_id != self.principal.user_id):
                raise ApiError("not_found", "That link reservation doesn't exist.")
            if slot.env != settings.plaid_env or slot.client_fp != fp:
                raise ApiError(
                    "conflict", "The Plaid keys or environment changed since this link began."
                )
            await assert_can_link(s, slot.env)
            was_linked, linked_connection = slot.state == "linked", slot.bank_connection_id

        # 2. Plaid creates the Item's access token. Plaintext lives only in ``item``.
        try:
            async with plaid_setup.open_client(creds) as plaid:
                item = await plaid.item_public_token_exchange(public_token)
        except PlaidError as exc:
            if was_linked and linked_connection and exc.error_code == "INVALID_PUBLIC_TOKEN":
                # Already exchanged, perhaps before a crash: the retry changes nothing.
                await self._ensure_links(linked_connection, creds, None)
                return linked_connection
            await self._audit_failure(
                "plaid.exchange", reservation_id, {"error_code": exc.error_code}
            )
            raise _plaid_api_error(exc) from None
        except PlaidUnavailable as exc:
            await self._audit_failure("plaid.exchange", reservation_id, {"reason": exc.reason})
            raise _plaid_api_error(exc) from None

        # 3. Seal and commit before /accounts/get.
        async with self._session() as s:
            await lock_household(s, hid)
            if settings.plaid_env == "sandbox":
                await assert_can_link(s, "sandbox")
            known = await s.scalar(
                select(PlaidLinkSlot).where(PlaidLinkSlot.item_id == item.item_id)
            )
            if known is not None:
                if known.bank_connection_id is None:
                    raise ApiError(
                        "conflict", "That Item was linked before and its connection is gone."
                    )
                connection_id = known.bank_connection_id
                self._audit(
                    s,
                    "plaid.exchange",
                    reservation_id,
                    {"connection_id": str(connection_id), "outcome": "already_linked"},
                )
            else:
                slot = (
                    await s.execute(
                        select(PlaidLinkSlot)
                        .where(PlaidLinkSlot.id == reservation_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if slot is None:
                    raise ApiError("not_found", "That link reservation doesn't exist.")
                connection_id = uuid.uuid4()
                sealed = crypto.seal(
                    item.access_token, self._conn_ctx(hid, connection_id), key=self._key()
                )
                s.add(
                    BankConnection(
                        id=connection_id,
                        household_id=hid,
                        provider="plaid",
                        external_id=item.item_id,
                        access_token_enc=sealed,
                        env=slot.env,
                        products=[],
                        status="pending_setup",
                        client_fp=fp,
                    )
                )
                try:
                    await s.flush()
                    outcome = await self._attach_slot(s, slot, item.item_id, connection_id)
                except IntegrityError:
                    raise ApiError("conflict", "That Item is already linked elsewhere.") from None
                self._audit(
                    s,
                    "plaid.exchange",
                    reservation_id,
                    {"connection_id": str(connection_id), "outcome": outcome},
                )
        await self._ensure_links(connection_id, creds, item.access_token)
        return connection_id

    async def _attach_slot(
        self, s: AsyncSession, slot: PlaidLinkSlot, item_id: str, connection_id: uuid.UUID
    ) -> str:
        """Mark the reservation ``linked``, or count a second Item as its own flagged row."""
        now = datetime.now(UTC)
        if slot.state == "reserved":
            slot.state = "linked"
            slot.item_id = item_id
            slot.bank_connection_id = connection_id
            slot.link_token_enc = None
            slot.resolved_at = now
            await s.flush()
            return "linked"
        s.add(
            PlaidLinkSlot(
                id=uuid.uuid4(),
                household_id=slot.household_id,
                client_fp=slot.client_fp,
                env=slot.env,
                state="linked",
                owner_count=slot.owner_count,
                item_id=item_id,
                bank_connection_id=connection_id,
                created_by_user_id=self.principal.user_id,
                resolved_at=now,
                flag=SECOND_PUBLIC_TOKEN,
            )
        )
        await s.flush()
        return SECOND_PUBLIC_TOKEN

    async def _ensure_links(
        self,
        connection_id: uuid.UUID,
        creds: PlaidCredentials,
        access: SecretToken | None,
    ) -> None:
        """Create the ``pending`` account links once. A failure here is audited, not raised:
        the Item is recorded, and a retried exchange fills the links in."""
        hid = self.principal.household_id
        async with self._session() as s:
            have = await s.scalar(
                select(func.count())
                .select_from(ConnectorAccountLink)
                .where(ConnectorAccountLink.bank_connection_id == connection_id)
            )
            if have:
                return
            if access is None:
                conn = await s.get(BankConnection, connection_id)
                if conn is None:
                    return
                try:
                    access = crypto.open(
                        conn.access_token_enc, self._conn_ctx(hid, conn.id), key=self._key()
                    )
                except crypto.CryptoError:
                    return
        try:
            async with plaid_setup.open_client(creds) as plaid:
                accounts = await plaid.accounts_get(access)
        except (PlaidError, PlaidUnavailable) as exc:
            await self._audit_failure(
                "plaid.accounts_get",
                None,
                {"connection_id": str(connection_id), "error": type(exc).__name__},
            )
            return
        ids = sorted({a["account_id"] for a in accounts if _external_id_ok(a.get("account_id"))})
        async with self._session() as s:
            for external in ids:
                s.add(
                    ConnectorAccountLink(
                        id=uuid.uuid4(),
                        household_id=hid,
                        bank_connection_id=connection_id,
                        external_account_id=external,
                        state="pending",
                    )
                )
            self._audit(
                s,
                "plaid.accounts_get",
                None,
                {"connection_id": str(connection_id), "accounts": len(ids)},
            )

    # ------------------------------------------------------------------ resolve and abandon

    async def resolve(self, reservation_id: uuid.UUID) -> SlotOut:
        """Ask Plaid what became of the reservation's Link token. The browser's ``onExit``
        only triggers this call: it decides nothing by itself."""
        require_owner(self.principal)
        hid = self.principal.household_id
        creds, fp = self._credentials()
        async with self._session() as s:
            slot = await s.get(PlaidLinkSlot, reservation_id)
            if slot is None:
                raise ApiError("not_found", "That link reservation doesn't exist.")
            if slot.state != "reserved" or slot.link_token_enc is None:
                return _slot_out(slot)
            if slot.env != self.ctx.settings.plaid_env or slot.client_fp != fp:
                raise ApiError(
                    "conflict", "The Plaid keys or environment changed since this link began."
                )
            try:
                token = crypto.open(
                    slot.link_token_enc, self._slot_ctx(hid, slot.id), key=self._key()
                )
            except crypto.CryptoError:
                raise ApiError(
                    "conflict", "That reservation's saved token can't be read."
                ) from None
        try:
            async with plaid_setup.open_client(creds) as plaid:
                data = await plaid.link_token_get(token)
        except (PlaidError, PlaidUnavailable) as exc:
            await self._audit_failure(
                "plaid.link_token_get", reservation_id, {"error": type(exc).__name__}
            )
            return await self._state(reservation_id)  # unchanged: the reservation stays open
        outcome = parse_link_sessions(data)
        del data
        if outcome.public_tokens:
            for public in outcome.public_tokens:
                await self._record_item(reservation_id, public, creator_only=False)
            result = await self._state(reservation_id)
        elif outcome.finished_without_item:
            result = await self._close(reservation_id, "abandoned", "plaid.link_token_get")
        else:
            result = await self._state(reservation_id)
        await self._audit_result("plaid.link_token_get", reservation_id, result.state)
        return result

    async def _audit_result(self, action: str, slot_id: uuid.UUID, state: str) -> None:
        await audit.record(
            self.ctx.factory,
            action,
            household_id=self.principal.household_id,
            user_id=self.principal.user_id,
            ip=self.ctx.ip,
            entity="plaid_link_slot",
            entity_id=slot_id,
            detail={"state": state},
        )

    async def _state(self, reservation_id: uuid.UUID) -> SlotOut:
        async with self._session() as s:
            slot = await s.get(PlaidLinkSlot, reservation_id, populate_existing=True)
            if slot is None:
                raise ApiError("not_found", "That link reservation doesn't exist.")
            return _slot_out(slot)

    async def _close(self, reservation_id: uuid.UUID, state: str, action: str) -> SlotOut:
        """Move an open reservation to ``abandoned``, dropping its sealed token in one UPDATE."""
        async with self._session() as s:
            await lock_household(s, self.principal.household_id)
            slot = (
                await s.execute(
                    select(PlaidLinkSlot)
                    .where(PlaidLinkSlot.id == reservation_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            if slot is None:
                raise ApiError("not_found", "That link reservation doesn't exist.")
            if slot.state in {"reserved", "unknown"}:
                slot.state = state
                slot.link_token_enc = None
                if slot.resolved_at is None:
                    slot.resolved_at = datetime.now(UTC)
                await s.flush()
            return _slot_out(slot)

    async def abandon(self, reservation_id: uuid.UUID, body: AbandonIn) -> SlotOut:
        """The owner says no Item was made. Needs step-up and the exact phrase."""
        require_owner(self.principal)
        require_fresh(self.principal)
        if body.confirm_phrase != ABANDON_PHRASE:
            raise ApiError("confirm_required", "Type the confirmation phrase exactly.")
        hid = self.principal.household_id
        async with self._session() as s:
            slot = await s.get(PlaidLinkSlot, reservation_id)
            if slot is None:
                raise ApiError("not_found", "That link reservation doesn't exist.")
            if slot.state not in {"reserved", "unknown"}:
                raise ApiError("conflict", "That reservation is already settled.")
            sealed, env = slot.link_token_enc, slot.env
        if sealed is not None and env == self.ctx.settings.plaid_env:
            # Best effort: if Plaid can say Link did create an Item, refuse to forget it.
            creds, _fp = self._credentials()
            try:
                token = crypto.open(sealed, self._slot_ctx(hid, reservation_id), key=self._key())
                async with plaid_setup.open_client(creds) as plaid:
                    data = await plaid.link_token_get(token)
                if parse_link_sessions(data).public_tokens:
                    raise ApiError(
                        "conflict", "Link created an Item. Resolve the reservation instead."
                    )
            except (PlaidError, PlaidUnavailable, crypto.CryptoError):
                pass
        result = await self._close(reservation_id, "abandoned", "plaid.reservation_abandon")
        await self._audit_result("plaid.reservation_abandon", reservation_id, result.state)
        return result
