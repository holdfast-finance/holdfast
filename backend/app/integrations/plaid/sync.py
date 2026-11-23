"""``/transactions/sync`` pagination into one :class:`SyncBatch` (roadmap P5a).

``fetch_sync`` reads only. It writes no ledger row and no cursor: the caller (P5b) persists
``next_cursor`` after the batch commits. Pages are collected in memory, and a mutation during
pagination discards them and restarts from the cursor the caller passed in (Plaid's documented
recovery), at most :data:`MAX_RESTARTS` times.

No response body is logged here or kept in an exception: errors become :class:`ConnectorError`
with a kind, a code and Plaid's request ID only.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import structlog

from app.domain.connector import (
    ConnectorAccount,
    ConnectorBalance,
    ConnectorTxn,
    SyncBatch,
)
from app.integrations.base import ConnectorError
from app.integrations.plaid.client import PlaidClient, PlaidError, PlaidUnavailable
from app.integrations.plaid.mapping import (
    MappingError,
    map_account,
    map_balance,
    map_removed,
    map_transaction,
)
from app.security.secret_token import SecretToken

log = structlog.get_logger(__name__)

SYNC_PATH = "/transactions/sync"
PAGE_SIZE = 500
MAX_PAGES = 40  # 20,000 rows per fetch; past it the batch says has_more and P5b fetches again
MAX_RESTARTS = 3
MUTATION_CODE = "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION"
HISTORY_DONE = "HISTORICAL_UPDATE_COMPLETE"

_AUTH_CODES = frozenset({"ITEM_LOGIN_REQUIRED", "PENDING_EXPIRATION", "ITEM_LOCKED"})
_DOWN_CODES = frozenset(
    {"INSTITUTION_DOWN", "INSTITUTION_NOT_RESPONDING", "INSTITUTION_NOT_AVAILABLE"}
)


def classify(error: PlaidError | PlaidUnavailable) -> ConnectorError:
    """Plaid failure to a ConnectorError. Codes only, never Plaid's message."""
    if isinstance(error, PlaidUnavailable):
        return ConnectorError("retryable", "PLAID_UNAVAILABLE", None)
    code, kind = error.error_code, "permanent"
    if code in _AUTH_CODES:
        kind = "auth_required"
    elif code in _DOWN_CODES:
        kind = "institution_down"
    elif error.error_type == "RATE_LIMIT_ERROR" or error.http_status == 429:
        kind = "rate_limited"
    elif "CURSOR" in code:
        kind = "invalid_cursor"
    elif error.error_type in {"INVALID_REQUEST", "INVALID_INPUT"}:
        kind = "invalid_request"
    elif (
        error.http_status >= 500
        or error.error_type == "API_ERROR"
        or code in {MUTATION_CODE, "PRODUCT_NOT_READY"}
    ):
        kind = "retryable"
    return ConnectorError(kind, code, error.request_id)


async def fetch_sync(
    plaid: PlaidClient,
    credential: SecretToken,
    cursor: str | None,
    *,
    max_pages: int = MAX_PAGES,
    today: Callable[[], date] | None = None,
) -> SyncBatch:
    """Read ``/transactions/sync`` from ``cursor`` until Plaid has no more, or ``max_pages``."""
    as_of = (today or (lambda: datetime.now(UTC).date()))()
    for _attempt in range(MAX_RESTARTS + 1):
        try:
            return await _paginate(plaid, credential, cursor, max_pages, as_of)
        except PlaidError as error:
            if error.error_code != MUTATION_CODE:
                raise classify(error) from None
            log.info("plaid_sync_mutation_restart", request_id=error.request_id)
        except PlaidUnavailable as error:
            raise classify(error) from None
        except MappingError:
            raise ConnectorError("permanent", "UNMAPPABLE_RESPONSE", None) from None
        except ValueError:
            # A connector value constructor refused a Plaid value (more than two decimals).
            raise ConnectorError("permanent", "UNMAPPABLE_RESPONSE", None) from None
    raise ConnectorError("retryable", MUTATION_CODE, None)


async def _paginate(
    plaid: PlaidClient,
    credential: SecretToken,
    start: str | None,
    max_pages: int,
    as_of: date,
) -> SyncBatch:
    accounts: dict[str, ConnectorAccount] = {}
    balances: dict[str, ConnectorBalance] = {}
    added: list[ConnectorTxn] = []
    modified: list[ConnectorTxn] = []
    removed: list[tuple[str, str]] = []
    cursor = start
    has_more = True
    status: object = None
    pages = 0
    while has_more and pages < max_pages:
        body: dict[str, Any] = {
            "access_token": credential.reveal(),
            "count": PAGE_SIZE,
            "options": {"include_original_description": True},
        }
        if cursor:
            body["cursor"] = cursor
        page = await plaid.post(SYNC_PATH, body)
        pages += 1
        for row in _rows(page, "accounts"):
            account = map_account(row)
            accounts[account.external_account_id] = account
            balance = map_balance(row, as_of)
            if balance is not None:
                balances[balance.external_account_id] = balance
        added.extend(map_transaction(r) for r in _rows(page, "added"))
        modified.extend(map_transaction(r) for r in _rows(page, "modified"))
        removed.extend(map_removed(r) for r in _rows(page, "removed"))
        next_cursor = page.get("next_cursor")
        if isinstance(next_cursor, str) and next_cursor:
            cursor = next_cursor
        has_more = page.get("has_more") is True
        status = page.get("transactions_update_status")
        del page
    return SyncBatch(
        accounts=tuple(accounts.values()),
        balances=tuple(balances.values()),
        added=tuple(added),
        modified=tuple(modified),
        removed=tuple(removed),
        next_cursor=cursor or None,
        has_more=has_more,
        history_complete=(not has_more and status == HISTORY_DONE),
    )


def _rows(page: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = page.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise MappingError(f"{key} is not a list")
    return [row for row in value if isinstance(row, dict)]
