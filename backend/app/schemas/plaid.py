"""Plaid setup checklist read schema (``/api/plaid/setup``). Carries no key material."""

from __future__ import annotations

from typing import Literal

from app.schemas.common import Model

PlaidEnvName = Literal["sandbox", "production"]
StepId = Literal["keys_present", "keys_valid", "products", "redirect_uri", "item_slots"]
StepState = Literal["done", "action", "blocked", "optional", "unchecked", "warn"]
ProductState = Literal["enabled", "not_enabled", "implicit", "unchecked", "error", "not_allowed"]


class PlaidSetupStep(Model):
    id: StepId
    state: StepState
    # Machine-readable: a Plaid ``error_code`` or one of the checklist's own reasons.
    reason: str | None
    # Plaid's request id for a failed probe, for a support ticket. Not a secret.
    request_id: str | None


class PlaidProductStatus(Model):
    product: str
    state: ProductState
    reason: str | None


class PlaidSetupOut(Model):
    env: PlaidEnvName
    # True when Link can be opened: keys verified, at least one product, no blocker.
    ready: bool
    # False until a live check has run in this response.
    checked: bool
    steps: list[PlaidSetupStep]
    products: list[PlaidProductStatus]
    redirect_uri: str | None
    suggested_redirect_uri: str
    item_slots_used: int
    item_slots_total: int
    # Plaid Items whose saved credential no longer opens (a lost or changed FERNET_KEY).
    tokens_unreadable: int
    # Ledger rows that say an Item was linked but have no connection (deleted or restored away).
    ledger_orphans: int
    # Codes, for example ``webhook_url_set``: Holdfast sends no webhook.
    warnings: list[str]
