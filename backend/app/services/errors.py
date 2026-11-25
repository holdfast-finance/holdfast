"""Errors services raise; ``app.api.errors`` renders them as ``{"error": {code, message}}``.

Codes and statuses match ``web/src/api/types.ts`` ``ERROR_CODES`` exactly.
"""

from __future__ import annotations

STATUS: dict[str, int] = {
    "unauthenticated": 401,
    "reauth_required": 403,
    "forbidden": 403,
    "csrf_failed": 403,
    "invalid_setup_token": 403,
    "setup_complete": 409,
    "invalid_credentials": 401,
    "invalid_code": 400,
    "invalid_invite": 404,
    "passkey_verification_failed": 400,
    "last_credential": 409,
    "conflict": 409,
    "busy": 409,
    # Plaid Link (P4-link, ADR 0034, ADR 0033 section 7).
    "confirm_required": 409,
    "no_slots_left": 409,
    "product_not_allowed": 409,
    "days_not_allowed": 409,
    "sandbox_on_real_data": 409,
    "sandbox_connection_present": 409,
    "plaid_error": 409,
    "plaid_unavailable": 502,
    "not_found": 404,
    "rate_limited": 429,
    "validation_error": 422,
    "payload_too_large": 413,
    "server_error": 500,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, *, headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = STATUS[code]
        self.headers = headers
