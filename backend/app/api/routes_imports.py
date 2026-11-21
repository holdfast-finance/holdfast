"""``/api/imports``: upload a statement PDF, list import runs, accept a run that needs review."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api.deps import Ctx, CurrentPrincipal, get_principal
from app.schemas.imports import ImportResultOut, ImportRunOut, ImportRunPage
from app.services.auth import require_fresh
from app.services.errors import ApiError
from app.services.imports import DEFAULT_LIMIT, MAX_LIMIT, ImportService

router = APIRouter(prefix="/imports", tags=["imports"])

_UPLOAD_BODY: dict[str, Any] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }
        }
    },
}


@router.post("/statements", openapi_extra={"requestBody": _UPLOAD_BODY})
async def upload_statement(request: Request, ctx: Ctx) -> ImportResultOut:
    # Authenticate before reading the body: FastAPI's own File() parameter would spool the whole
    # upload to disk before the session was checked.
    principal = await get_principal(request)
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise ApiError("validation_error", "Send the PDF as multipart form data.")
    try:
        form = await request.form(max_files=1, max_fields=4)
    except MultiPartException:
        raise ApiError("validation_error", "That upload couldn't be read.") from None
    try:
        part = form.get("file")
        if not isinstance(part, UploadFile):
            raise ApiError("validation_error", "Attach one PDF as the `file` part.")
        return await ImportService(ctx, principal).upload(part, part.filename)
    finally:
        await form.close()


@router.get("/runs")
async def list_runs(
    ctx: Ctx,
    principal: CurrentPrincipal,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> ImportRunPage:
    return await ImportService(ctx, principal).runs(limit=limit, cursor=cursor)


@router.post("/runs/{run_id}/accept")
async def accept_run(run_id: uuid.UUID, ctx: Ctx, principal: CurrentPrincipal) -> ImportRunOut:
    # Accepting overrides a reconciliation flag (hard rule 6), so it needs a fresh step-up.
    require_fresh(principal)
    return await ImportService(ctx, principal).accept(run_id)
