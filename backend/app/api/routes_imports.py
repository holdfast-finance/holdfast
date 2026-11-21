"""``/api/imports``: upload a statement PDF, list import runs, accept a run that needs review."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api.deps import Ctx, CurrentPrincipal, get_principal
from app.schemas.imports import ImportResultOut, ImportRunOut, ImportRunPage
from app.services import imports as import_service
from app.services.auth import require_fresh
from app.services.imports import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ImportService,
    parse_slot,
)

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
    # Order matters: session, then the per-user rate limit, then (one of two) parse slots, and only
    # then the body. FastAPI's own File() parameter would spool the whole upload to disk before
    # any of these ran.
    principal = await get_principal(request)
    service = ImportService(ctx, principal)
    await service.throttle()
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        await service.refuse(
            "not_multipart", "validation_error", "Send the PDF as multipart form data."
        )
    token = await service.begin()  # one upload in flight per user
    try:
        async with parse_slot() as slot:
            try:
                async with asyncio.timeout(import_service.FORM_READ_SECONDS):
                    form = await request.form(max_files=1, max_fields=4)
            except TimeoutError:
                await service.refuse(
                    "body_timeout", "validation_error", "That upload took too long to arrive."
                )
            except MultiPartException:
                await service.refuse(
                    "bad_form", "validation_error", "That upload couldn't be read."
                )
            try:
                part = form.get("file")
                if not isinstance(part, UploadFile):
                    await service.refuse(
                        "no_file", "validation_error", "Attach one PDF as the `file` part."
                    )
                return await service.upload(part, part.filename, slot)
            finally:
                await form.close()
    finally:
        await service.end(token)


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
