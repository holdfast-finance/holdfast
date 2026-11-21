"""Plaid setup checklist: turn configuration facts and probe outcomes into ordered steps.

Pure (ADR 0011). The service gathers the facts (what is configured, what Plaid answered); this
module decides what each step's state is and what the person has to do next. Copy lives in the
web app; every step here is an id, a state and a machine-readable reason.

Plaid cannot be configured *for* a self-hosted household: account sign-up, the Trial plan
application and the redirect-URI allowlist exist only in Plaid's dashboard. What can be done is
to verify each of those manual steps against the live API, which is what the probes are for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

StepState = Literal["done", "action", "blocked", "optional", "unchecked"]
ProbeResult = Literal["ok", "rejected", "unreachable", "error"]

# Never valid in a Link token's ``products``: Balance is implied by every Item.
IMPLICIT_PRODUCTS: frozenset[str] = frozenset({"balance"})
# Trial plan cap (PLAN 3.2). Removing an Item does not free a slot.
ITEM_SLOTS = 10
SLOT_CONFIRM_AT = 8


@dataclass(frozen=True)
class Probe:
    """One live call's outcome. ``code`` is Plaid's ``error_code`` or a transport reason."""

    result: ProbeResult
    code: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class PreflightFacts:
    env: Literal["sandbox", "production"]
    has_client_id: bool
    has_secret: bool
    products: tuple[str, ...]
    redirect_uri: str | None
    # Where Link's OAuth return route lives on this install; what the person should register.
    suggested_redirect_uri: str
    production_items_used: int = 0
    # Probes are None until a live check has run.
    keys: Probe | None = None
    product_probes: dict[str, Probe] = field(default_factory=dict)
    redirect: Probe | None = None


@dataclass(frozen=True)
class Step:
    id: str
    state: StepState
    reason: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class ProductStatus:
    product: str
    state: Literal["enabled", "not_enabled", "implicit", "unchecked", "error"]
    reason: str | None = None


@dataclass(frozen=True)
class Preflight:
    env: Literal["sandbox", "production"]
    ready: bool
    checked: bool
    steps: tuple[Step, ...]
    products: tuple[ProductStatus, ...]
    redirect_uri: str | None
    suggested_redirect_uri: str
    item_slots_used: int
    item_slots_total: int


def requestable_products(products: tuple[str, ...]) -> tuple[str, ...]:
    """The configured products that may go in a Link token, in order, without duplicates."""
    seen: list[str] = []
    for p in products:
        if p not in IMPLICIT_PRODUCTS and p not in seen:
            seen.append(p)
    return tuple(seen)


def redirect_uri_problem(uri: str, env: str) -> str | None:
    """Why Plaid would refuse ``uri`` before it is even looked up, or None.

    Plaid allows ``http://localhost`` in Sandbox only; Production needs HTTPS. No query string and
    no fragment in either.
    """
    parts = urlsplit(uri)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return "not_a_url"
    if parts.query or parts.fragment:
        return "has_query_or_fragment"
    if parts.scheme == "http":
        if env == "production":
            return "https_required"
        if parts.hostname not in {"localhost", "127.0.0.1"}:
            return "http_only_on_localhost"
    return None


def _failed(step_id: str, probe: Probe) -> Step:
    if probe.result == "unreachable":
        return Step(step_id, "blocked", "plaid_unreachable")
    return Step(step_id, "action", probe.code or "unknown", probe.request_id)


def _keys_step(facts: PreflightFacts) -> Step:
    if facts.keys is None:
        return Step("keys_valid", "unchecked")
    if facts.keys.result == "ok":
        return Step("keys_valid", "done")
    return _failed("keys_valid", facts.keys)


def _product_statuses(facts: PreflightFacts) -> tuple[ProductStatus, ...]:
    out: list[ProductStatus] = []
    for product in dict.fromkeys(facts.products):
        if product in IMPLICIT_PRODUCTS:
            out.append(ProductStatus(product, "implicit"))
            continue
        probe = facts.product_probes.get(product)
        if probe is None:
            out.append(ProductStatus(product, "unchecked"))
        elif probe.result == "ok":
            out.append(ProductStatus(product, "enabled"))
        elif probe.code == "INVALID_PRODUCT":
            out.append(ProductStatus(product, "not_enabled", probe.code))
        else:
            out.append(ProductStatus(product, "error", probe.code or probe.result))
    return tuple(out)


def _products_step(statuses: tuple[ProductStatus, ...]) -> Step:
    asked = [s for s in statuses if s.state != "implicit"]
    if not asked:
        return Step("products", "action", "no_products_configured")
    if all(s.state == "unchecked" for s in asked):
        return Step("products", "unchecked")
    if any(s.state == "enabled" for s in asked):
        missing = any(s.state in {"not_enabled", "error"} for s in asked)
        # A missing product is not a failure: it is switched off and the rest still sync.
        return Step("products", "done", "some_products_unavailable" if missing else None)
    if any(s.state == "error" for s in asked):
        return Step("products", "blocked", "product_check_failed")
    return Step("products", "action", "no_products_enabled")


def _redirect_step(facts: PreflightFacts) -> Step:
    if facts.redirect_uri is None:
        # Desktop browsers finish OAuth in a pop-up without one. Phones need it.
        return Step("redirect_uri", "optional", "not_set")
    problem = redirect_uri_problem(facts.redirect_uri, facts.env)
    if problem is not None:
        return Step("redirect_uri", "action", problem)
    if facts.redirect is None:
        return Step("redirect_uri", "unchecked")
    if facts.redirect.result == "ok":
        return Step("redirect_uri", "done")
    if facts.redirect.code == "INVALID_FIELD":
        return Step("redirect_uri", "action", "not_registered", facts.redirect.request_id)
    return _failed("redirect_uri", facts.redirect)


def _slots_step(facts: PreflightFacts) -> Step:
    if facts.env == "sandbox":
        return Step("item_slots", "optional", "sandbox_is_unlimited")
    used = facts.production_items_used
    if used >= ITEM_SLOTS:
        return Step("item_slots", "blocked", "no_slots_left")
    if used >= SLOT_CONFIRM_AT:
        return Step("item_slots", "done", "few_slots_left")
    return Step("item_slots", "done")


def evaluate(facts: PreflightFacts) -> Preflight:
    """Build the checklist. Steps are ordered the way a person works through them."""
    configured = facts.has_client_id and facts.has_secret
    if not facts.has_client_id:
        present = Step("keys_present", "action", "client_id_missing")
    elif not facts.has_secret:
        present = Step("keys_present", "action", f"secret_{facts.env}_missing")
    else:
        present = Step("keys_present", "done")

    statuses = _product_statuses(facts)
    if configured:
        keys = _keys_step(facts)
        keys_ok = keys.state == "done"
        # Product and redirect answers mean nothing while the keys themselves are refused.
        products = (
            _products_step(statuses)
            if keys_ok or facts.keys is None
            else Step("products", "unchecked")
        )
        redirect = (
            _redirect_step(facts)
            if keys_ok or facts.keys is None
            else Step("redirect_uri", "unchecked")
        )
    else:
        keys = Step("keys_valid", "unchecked")
        products = Step("products", "unchecked")
        redirect = Step("redirect_uri", "unchecked")
    slots = _slots_step(facts)

    steps = (present, keys, products, redirect, slots)
    checked = configured and facts.keys is not None
    ready = (
        present.state == "done"
        and keys.state == "done"
        and products.state == "done"
        and redirect.state in {"done", "optional"}
        and slots.state != "blocked"
    )
    return Preflight(
        env=facts.env,
        ready=ready,
        checked=checked,
        steps=steps,
        products=statuses,
        redirect_uri=facts.redirect_uri,
        suggested_redirect_uri=facts.suggested_redirect_uri,
        item_slots_used=0 if facts.env == "sandbox" else facts.production_items_used,
        item_slots_total=ITEM_SLOTS,
    )
