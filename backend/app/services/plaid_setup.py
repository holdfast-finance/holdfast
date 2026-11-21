"""Plaid setup checklist: what is configured, and whether Plaid agrees (roadmap P3).

``status`` reads configuration only. ``check`` asks Plaid: one free ``/institutions/get`` proves
the keys, one ``/link/token/create`` per product shows which products the account has, and one
more with the redirect URI shows whether it is on the dashboard allowlist. A Link token creates
no Item and uses no Trial slot; the tokens made here are dropped, never returned or stored.

Nothing here returns, logs or audits a key. The response says only whether a key is present.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db_context import scoped_session
from app.domain import plaid_preflight as pf
from app.integrations.plaid.client import (
    PlaidClient,
    PlaidCredentials,
    PlaidError,
    PlaidUnavailable,
)
from app.models import BankConnection
from app.schemas.plaid import PlaidSetupOut
from app.security.ratelimit import RateLimiter
from app.security.secret_token import SecretToken
from app.services import audit
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.household import require_owner
from app.services.sessions import Principal

OAUTH_RETURN_PATH = "/plaid/oauth-return"
CLIENT_NAME = "Holdfast"
# Each check is up to seven Plaid calls; Sandbox allows ten /institutions/get a minute.
CHECKS_PER_WINDOW = 6
CHECK_WINDOW_SECONDS = 300
STATEMENTS_PROBE_DAYS = 90


def _plaid_keys(settings: Settings) -> tuple[str, str]:
    """(client id, the active environment's secret), stripped; ``""`` where unset.

    The only read of the Plaid keys. Self-host reads the environment (ADR 0015); when that ADR's
    resolver lands, this is the one function that changes.
    """
    secret = (
        settings.plaid_secret_production
        if settings.plaid_env == "production"
        else settings.plaid_secret_sandbox
    )
    client_id = (settings.plaid_client_id or "").strip()
    return client_id, secret.get_secret_value().strip() if secret is not None else ""


def plaid_credentials(settings: Settings) -> PlaidCredentials | None:
    """The active environment's keys, or None when either is missing."""
    client_id, secret = _plaid_keys(settings)
    if not client_id or not secret:
        return None
    return PlaidCredentials(
        env=settings.plaid_env, client_id=SecretToken(client_id), secret=SecretToken(secret)
    )


def open_client(credentials: PlaidCredentials) -> PlaidClient:
    """Seam for tests, which replace this with a client on a recorded transport."""
    return PlaidClient(credentials)


def _probe_error(exc: PlaidError | PlaidUnavailable) -> pf.Probe:
    if isinstance(exc, PlaidUnavailable):
        if exc.reason == "bad_credentials":
            # Nothing was sent: the key itself cannot be put in a request.
            return pf.Probe("rejected", "keys_malformed")
        return pf.Probe("unreachable", exc.reason)
    return pf.Probe("rejected", exc.error_code, exc.request_id)


class PlaidSetupService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    def _session(self) -> contextlib.AbstractAsyncContextManager[AsyncSession]:
        return scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        )

    async def _production_items(self) -> int:
        async with self._session() as s:
            count = await s.scalar(
                select(func.count())
                .select_from(BankConnection)
                .where(BankConnection.provider == "plaid", BankConnection.env == "production")
            )
        return int(count or 0)

    async def _facts(self) -> pf.PreflightFacts:
        settings = self.ctx.settings
        client_id, secret = _plaid_keys(settings)
        return pf.PreflightFacts(
            env=settings.plaid_env,
            has_client_id=bool(client_id),
            has_secret=bool(secret),
            products=tuple(settings.plaid_products),
            redirect_uri=settings.plaid_redirect_uri or None,
            suggested_redirect_uri=settings.app_base_url.rstrip("/") + OAUTH_RETURN_PATH,
            production_items_used=await self._production_items(),
        )

    async def status(self) -> PlaidSetupOut:
        """The checklist from configuration alone. No call to Plaid."""
        require_owner(self.principal)
        return _out(pf.evaluate(await self._facts()))

    async def check(self) -> PlaidSetupOut:
        """Run the live probes and return the checklist with their results."""
        require_owner(self.principal)
        limited = await RateLimiter(self.ctx.redis).throttle(
            f"plaid-setup:{self.principal.household_id}",
            limit=CHECKS_PER_WINDOW,
            window=CHECK_WINDOW_SECONDS,
        )
        if limited is not None:
            raise ApiError(
                "rate_limited", limited.message, headers={"Retry-After": str(limited.retry_after)}
            )
        facts = await self._facts()
        credentials = plaid_credentials(self.ctx.settings)
        if credentials is not None:
            async with open_client(credentials) as plaid:
                facts = await _probe(plaid, facts, self.ctx.settings, datetime.now(UTC).date())
        result = pf.evaluate(facts)
        await audit.record(
            self.ctx.factory,
            "plaid.setup_check",
            household_id=self.principal.household_id,
            user_id=self.principal.user_id,
            ip=self.ctx.ip,
            outcome="success" if result.ready else "failure",
            detail={
                "env": result.env,
                "steps": {step.id: step.state for step in result.steps},
            },
        )
        return _out(result)


async def _probe(
    plaid: PlaidClient, facts: pf.PreflightFacts, settings: Settings, today: date
) -> pf.PreflightFacts:
    countries = list(settings.plaid_country_codes)
    try:
        await plaid.institutions_get(country_codes=countries)
        keys = pf.Probe("ok")
    except (PlaidError, PlaidUnavailable) as exc:
        keys = _probe_error(exc)
    if keys.result != "ok":
        return _with(facts, keys=keys)

    # A throwaway id: Plaid requires one, and no real person is behind a probe.
    user_id = f"holdfast-setup-{uuid.uuid4().hex}"
    statements = (
        (today - timedelta(days=STATEMENTS_PROBE_DAYS)).isoformat(),
        today.isoformat(),
    )

    async def token(products: list[str], redirect_uri: str | None = None) -> pf.Probe:
        try:
            # The token itself is discarded: it never leaves this function.
            await plaid.link_token_create(
                client_user_id=user_id,
                client_name=CLIENT_NAME,
                products=products,
                country_codes=countries,
                redirect_uri=redirect_uri,
                transactions_days_requested=settings.plaid_tx_days_requested,
                statements_range=statements,
            )
        except (PlaidError, PlaidUnavailable) as exc:
            return _probe_error(exc)
        return pf.Probe("ok")

    product_probes: dict[str, pf.Probe] = {}
    for product in pf.requestable_products(facts.products):
        product_probes[product] = await token([product])
    enabled = [p for p, probe in product_probes.items() if probe.result == "ok"]

    redirect: pf.Probe | None = None
    uri = facts.redirect_uri
    if uri is not None and enabled and pf.redirect_uri_problem(uri, facts.env) is None:
        # Asked with a product already known to work, so a refusal is about the URI alone.
        redirect = await token([enabled[0]], uri)
    return _with(facts, keys=keys, product_probes=product_probes, redirect=redirect)


def _with(
    facts: pf.PreflightFacts,
    *,
    keys: pf.Probe,
    product_probes: dict[str, pf.Probe] | None = None,
    redirect: pf.Probe | None = None,
) -> pf.PreflightFacts:
    return pf.PreflightFacts(
        env=facts.env,
        has_client_id=facts.has_client_id,
        has_secret=facts.has_secret,
        products=facts.products,
        redirect_uri=facts.redirect_uri,
        suggested_redirect_uri=facts.suggested_redirect_uri,
        production_items_used=facts.production_items_used,
        keys=keys,
        product_probes=product_probes or {},
        redirect=redirect,
    )


def _out(result: pf.Preflight) -> PlaidSetupOut:
    return PlaidSetupOut.model_validate(
        {
            "env": result.env,
            "ready": result.ready,
            "checked": result.checked,
            "steps": [
                {"id": s.id, "state": s.state, "reason": s.reason, "request_id": s.request_id}
                for s in result.steps
            ],
            "products": [
                {"product": p.product, "state": p.state, "reason": p.reason}
                for p in result.products
            ],
            "redirect_uri": result.redirect_uri,
            "suggested_redirect_uri": result.suggested_redirect_uri,
            "item_slots_used": result.item_slots_used,
            "item_slots_total": result.item_slots_total,
        }
    )
