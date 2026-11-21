"""Thin async Plaid HTTP client (PLAN 7; roadmap P3).

Why not ``plaid-python``: its generated models parse JSON numbers into ``float``. Money here is
``Decimal`` from the first byte (hard rule 1), so responses are decoded with
``parse_float=Decimal`` and returned as plain dicts.

Credentials travel in the ``PLAID-CLIENT-ID`` / ``PLAID-SECRET`` headers, never in the JSON body,
so a logged or echoed request body cannot carry them. Nothing in this module logs a request or
response body: errors log the path, HTTP status, ``error_type``, ``error_code`` and ``request_id``
only (hard rule 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal, Self

import httpx
import structlog

from app.security.secret_token import SecretToken

PlaidEnv = Literal["sandbox", "production"]

BASE_URLS: dict[PlaidEnv, str] = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}
# Pinned so a default-version change in the Plaid dashboard cannot change response shapes.
API_VERSION = "2020-09-14"
DEFAULT_TIMEOUT = httpx.Timeout(20, connect=10)
_ID_MAX = 64

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PlaidCredentials:
    """One environment's API keys. Both values stay wrapped until the request is built."""

    env: PlaidEnv
    client_id: SecretToken
    secret: SecretToken


class PlaidError(Exception):
    """Plaid answered with an error object. Carries codes only, never Plaid's free-text message.

    ``error_message`` can echo request fields, so it is dropped here and never logged or shown.
    """

    def __init__(
        self,
        *,
        path: str,
        http_status: int,
        error_type: str,
        error_code: str,
        request_id: str | None,
    ) -> None:
        super().__init__(f"plaid {path} failed: {error_type}/{error_code}")
        self.path = path
        self.http_status = http_status
        self.error_type = error_type
        self.error_code = error_code
        self.request_id = request_id


class PlaidUnavailable(Exception):
    """Plaid could not be reached, timed out, or sent something that is not a Plaid response."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"plaid {path} unavailable: {reason}")
        self.path = path
        self.reason = reason


def _reject_constant(name: str) -> Decimal:
    """``NaN`` / ``Infinity`` are not JSON and would come back as ``float``. Refuse them."""
    raise ValueError("non-finite number in a Plaid response")


def _code(value: object, fallback: str) -> str:
    """A Plaid code field, kept only when it looks like one (``UPPER_SNAKE``, bounded)."""
    if (
        isinstance(value, str)
        and 0 < len(value) <= _ID_MAX
        and value.replace("_", "").isalnum()
        and value.isascii()
    ):
        return value
    return fallback


def _request_id(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= _ID_MAX and value.isascii() and value.isalnum():
        return value
    return None


class PlaidClient:
    """One client per credential set. Use as ``async with PlaidClient(creds) as plaid``."""

    def __init__(
        self,
        credentials: PlaidCredentials,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self.env: PlaidEnv = credentials.env
        self._credentials = credentials
        self._http = httpx.AsyncClient(
            base_url=BASE_URLS[credentials.env],
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            headers={"Plaid-Version": API_VERSION, "User-Agent": "holdfast"},
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST ``body`` to ``path`` and return the decoded object (numbers with a fraction are
        ``Decimal``). Raises :class:`PlaidError` or :class:`PlaidUnavailable`."""
        headers = {
            # The only place the keys leave their wrappers.
            "PLAID-CLIENT-ID": self._credentials.client_id.reveal(),
            "PLAID-SECRET": self._credentials.secret.reveal(),
        }
        try:
            response = await self._http.post(path, json=body, headers=headers)
        except httpx.TimeoutException:
            raise self._unavailable(path, "timeout") from None
        except httpx.HTTPError:
            # ``from None``: the chained httpx error holds the request, headers included.
            raise self._unavailable(path, "network") from None
        except (UnicodeError, ValueError):
            # A key with a character that cannot go in a header (a stray non-ASCII byte from a
            # copy-paste). The original error quotes the offending character, so drop it.
            raise self._unavailable(path, "bad_credentials") from None
        try:
            data = json.loads(
                response.content, parse_float=Decimal, parse_constant=_reject_constant
            )
        except (ValueError, UnicodeDecodeError):
            raise self._unavailable(path, "bad_response") from None
        if not isinstance(data, dict):
            raise self._unavailable(path, "bad_response")
        if not response.is_success or "error_code" in data:
            error = PlaidError(
                path=path,
                http_status=response.status_code,
                error_type=_code(data.get("error_type"), "UNKNOWN"),
                error_code=_code(data.get("error_code"), "UNKNOWN"),
                request_id=_request_id(data.get("request_id")),
            )
            log.warning(
                "plaid_error",
                path=path,
                plaid_env=self.env,
                http_status=error.http_status,
                error_type=error.error_type,
                error_code=error.error_code,
                request_id=error.request_id,
            )
            raise error
        return data

    def _unavailable(self, path: str, reason: str) -> PlaidUnavailable:
        log.warning("plaid_unavailable", path=path, plaid_env=self.env, reason=reason)
        return PlaidUnavailable(path, reason)

    # ------------------------------------------------------------------ endpoints

    async def institutions_get(self, *, country_codes: list[str], count: int = 1) -> dict[str, Any]:
        """A free, authenticated call: the cheapest proof that the keys work in this env."""
        return await self.post(
            "/institutions/get", {"count": count, "offset": 0, "country_codes": country_codes}
        )

    async def link_token_create(
        self,
        *,
        client_user_id: str,
        client_name: str,
        products: list[str],
        country_codes: list[str],
        language: str = "en",
        redirect_uri: str | None = None,
        transactions_days_requested: int | None = None,
        statements_range: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a Link token. It creates no Item and uses no Trial slot."""
        body: dict[str, Any] = {
            "user": {"client_user_id": client_user_id},
            "client_name": client_name,
            "products": products,
            "country_codes": country_codes,
            "language": language,
        }
        if redirect_uri is not None:
            body["redirect_uri"] = redirect_uri
        if transactions_days_requested is not None and "transactions" in products:
            body["transactions"] = {"days_requested": transactions_days_requested}
        if statements_range is not None and "statements" in products:
            body["statements"] = {
                "start_date": statements_range[0],
                "end_date": statements_range[1],
            }
        return await self.post("/link/token/create", body)
