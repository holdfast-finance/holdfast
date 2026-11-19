"""The API's one error shape: ``{"error": {"code": "...", "message": "..."}}``.

Codes and statuses match ``web/src/api/types.ts`` ``ERROR_CODES`` exactly. Messages are short and
human, and never echo user input, tokens or internals.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import ApiError


def error_response(
    status: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}}, status_code=status, headers=headers
    )


def _field(loc: tuple[Any, ...]) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    return ".".join(parts)


def validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "That request isn't valid."
    first = errors[0]
    field = _field(tuple(first.get("loc", ())))
    ctx = first.get("ctx") or {}
    # Custom validators raise ValueError("..."): surface just our sentence.
    if first.get("type") == "value_error" and "error" in ctx:
        text = str(ctx["error"])
    elif first.get("type") == "missing":
        text = "is required"
    elif first.get("type") == "string_too_short":
        text = "is too short"
    elif first.get("type") == "string_too_long":
        text = "is too long"
    else:
        text = "is not valid"
    if field and not text[:1].isupper():
        return f"{field.replace('_', ' ').capitalize()} {text}."
    return text if text.endswith(".") else f"{text}."


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo the input back: it may be a password or a code.
        return error_response(422, "validation_error", validation_message(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(404, "not_found", "Not found.")
        if exc.status_code == 405:
            return error_response(405, "method_not_allowed", "Method not allowed.")
        return error_response(
            exc.status_code, "http_error", "Request failed.", dict(exc.headers or {})
        )
