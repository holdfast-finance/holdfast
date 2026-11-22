"""Plaid Link rules that need no I/O: which products a login kind asks for, and how to read a
``/link/token/get`` answer (PHASE-2 contract 5.1; ADR 0034 section 3).

Link products are an **allowlist**. ``auth`` would consent full account and routing numbers and
``identity`` the holder's personal details, so no phrase or setting enables them (data
minimization). ``balance`` is implicit in every Item and is never sent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.security.secret_token import SecretToken

LoginKind = Literal["bank_card", "retirement_hsa", "brokerage"]
LinkMode = Literal["new", "update"]

ALLOWED_PRODUCTS: frozenset[str] = frozenset({"transactions", "liabilities", "investments"})
# Accepted in PLAID_PRODUCTS without error, and never sent (ADR 0023).
IMPLICIT_PRODUCTS: frozenset[str] = frozenset({"balance"})
REQUIRED_DAYS = 730
SLOT_TOTAL = 10
# A Link token lives four hours; a reservation older than this moves to ``unknown``.
RESERVATION_TTL_HOURS = 4
ABANDON_PHRASE = "I did not finish this link"
# Where the owner's own Item count comes from. Plaid's dashboard may show only active Items,
# which under-count after removals, so the owner's written slot log is the named source.
# VERIFY on a Trial dashboard (ADR 0034 section 2, R2-S4).
COUNT_SOURCE = "owner_slot_log"
# Plaid's Trial Item-limit error. VERIFY the exact code; any of these maps to no_slots_left.
SLOT_LIMIT_ERROR_CODES: frozenset[str] = frozenset({"ITEM_LIMIT_EXCEEDED"})


@dataclass(frozen=True)
class LinkProducts:
    products: tuple[str, ...]
    optional_products: tuple[str, ...]
    days_requested: int | None


# login_kind -> (products, optional_products, days_requested)
_TABLE: dict[str, tuple[tuple[str, ...], tuple[str, ...], int | None]] = {
    "bank_card": (("transactions",), ("liabilities",), REQUIRED_DAYS),
    "retirement_hsa": (("investments",), (), None),
    "brokerage": (("investments",), (), None),
}


class ProductNotAllowedError(Exception):
    """A configured product is outside the allowlist, or the login needs one the plan lacks."""


class DaysNotAllowedError(Exception):
    """``PLAID_TX_DAYS_REQUESTED`` is not 730."""


def link_products(
    login_kind: str, configured: list[str] | tuple[str, ...], days_requested: int
) -> LinkProducts:
    """The table row for ``login_kind`` intersected with ``configured`` (PLAID_PRODUCTS).

    Raises :class:`ProductNotAllowedError` for any configured product off the allowlist (even
    one this login would not use) or when the plan lists none of the login's products, and
    :class:`DaysNotAllowedError` unless days is 730.
    """
    if any(p not in ALLOWED_PRODUCTS and p not in IMPLICIT_PRODUCTS for p in configured):
        raise ProductNotAllowedError
    if days_requested != REQUIRED_DAYS:
        raise DaysNotAllowedError
    row = _TABLE.get(login_kind)
    if row is None:
        raise ProductNotAllowedError
    products = tuple(p for p in row[0] if p in configured)
    if not products:
        raise ProductNotAllowedError
    optional = tuple(p for p in row[1] if p in configured)
    return LinkProducts(products, optional, row[2] if "transactions" in products else None)


def slot_phrase(count: int) -> str:
    """The exact phrase for taking slot ``count + 1`` of 10."""
    return f"use slot {count + 1} of {SLOT_TOTAL}"


@dataclass(frozen=True)
class LinkOutcome:
    """What ``/link/token/get`` says happened. Public tokens stay wrapped."""

    public_tokens: tuple[SecretToken, ...] = field(default=(), repr=False)
    # At least one session ran and every one finished with no Item added.
    finished_without_item: bool = False


def parse_link_sessions(data: dict[str, Any]) -> LinkOutcome:
    """Read the ``link_sessions`` of a ``/link/token/get`` answer. Tolerant of absent fields:
    anything not clearly "finished with no Item" leaves the reservation open."""
    sessions = data.get("link_sessions")
    if not isinstance(sessions, list):
        return LinkOutcome()
    tokens: list[SecretToken] = []
    all_finished = bool(sessions)
    for session in sessions:
        if not isinstance(session, dict):
            all_finished = False
            continue
        results = session.get("results")
        added = results.get("item_add_results") if isinstance(results, dict) else None
        for item in added if isinstance(added, list) else []:
            token = item.get("public_token") if isinstance(item, dict) else None
            if isinstance(token, str) and token:
                tokens.append(SecretToken(token))
        if not session.get("finished_at"):
            all_finished = False
    return LinkOutcome(
        public_tokens=tuple(tokens), finished_without_item=all_finished and not tokens
    )
