"""Statement upload, import-run listing and acceptance (I5b).

Request path only. Persisting a parsed statement is ``statement_import.import_statement`` (ADR
0011); this module stores the uploaded bytes, parses them, calls that one entry point, and reads
the runs back under the caller's row-level security.

Uploads: the file is streamed to a temp file in the document store while a SHA-256 and a hard
byte counter run (never trusting Content-Length). The final path is derived from the hash, never
from the client's filename, which is only sanitised and kept as a display label. Nothing here logs
a filename or statement text.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session
from app.domain.statement_parse import (
    ParsedStatement,
    StatementParseError,
    detect_profile,
    parse_statement,
)
from app.importers.pdf_text import MAX_PDF_BYTES, pdf_to_text
from app.importers.profile_loader import load_profiles
from app.models import Account, Document, ImportRun
from app.schemas.imports import (
    ImportBlockOut,
    ImportResultOut,
    ImportRunOut,
    ImportRunPage,
)
from app.security.ratelimit import RateLimiter
from app.services import audit
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.sessions import Principal
from app.services.statement_import import (
    AccountNotVisibleError,
    BlockResult,
    DocumentMeta,
    import_statement,
)

PDF_MAGIC = b"%PDF-"
ALLOWED_TYPES = frozenset({"application/pdf", "application/x-pdf", "application/octet-stream"})
UPLOADS_PER_MINUTE = 10
CHUNK = 64 * 1024
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
STORE_SUBDIR = "statements"
FILENAME_MAX = 100
_FILENAME_OK = re.compile(r"[^A-Za-z0-9 ._()\-]")


class _Upload(Protocol):
    """The part of Starlette's UploadFile used here (also what the tests pass)."""

    @property
    def content_type(self) -> str | None: ...

    async def read(self, size: int = -1, /) -> bytes: ...


def sanitise_filename(raw: str | None) -> str:
    """A display label: base name only, safe characters, bounded, always ``.pdf``."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _FILENAME_OK.sub("_", name).strip(" .")
    name = re.sub(r"_{2,}", "_", name)
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    stem = stem.strip(" ._")[: FILENAME_MAX - 4]
    return f"{stem or 'statement'}.pdf"


def _encode_cursor(at: datetime, run_id: uuid.UUID) -> str:
    raw = json.dumps([at.isoformat(), str(run_id)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at, run_id = json.loads(raw)
        if not isinstance(at, str) or not isinstance(run_id, str):
            raise TypeError("cursor parts must be strings")
        parsed = datetime.fromisoformat(at)
        if parsed.tzinfo is None:
            raise ValueError("naive cursor time")
        return parsed, uuid.UUID(run_id)
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError, OverflowError):
        raise ApiError("validation_error", "That page cursor isn't valid.") from None


def _parse_pdf(path: Path) -> ParsedStatement:
    """CPU-bound: run in a thread. Any failure becomes one fixed, content-free message."""
    try:
        text = pdf_to_text(path)
        profile = detect_profile(text, load_profiles())
        if profile is None:
            raise ApiError(
                "validation_error", "Holdfast doesn't have a profile for this statement yet."
            )
        return parse_statement(text, profile)
    except ApiError:
        raise
    except (StatementParseError, ValueError):
        raise ApiError("validation_error", "That statement couldn't be read.") from None
    except Exception:  # pdfplumber / pdfminer raise many types on malformed files
        raise ApiError("validation_error", "That file isn't a readable PDF.") from None


async def _stream_to_temp(upload: _Upload, directory: Path) -> tuple[Path, str, int]:
    """Copy the upload to a private temp file: (path, sha256 hex, size). Hard-capped."""
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True, mode=0o700)
    tmp = directory / f".upload-{uuid.uuid4().hex}.pdf"
    digest = hashlib.sha256()
    size = 0
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            first = True
            while chunk := await upload.read(CHUNK):
                if first:
                    first = False
                    if not chunk.startswith(PDF_MAGIC):
                        raise ApiError("validation_error", "That file isn't a PDF.")
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise ApiError("payload_too_large", "That PDF is over the 20 MiB limit.")
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if size == 0:
        tmp.unlink(missing_ok=True)
        raise ApiError("validation_error", "That file is empty.")
    return tmp, digest.hexdigest(), size


class ImportService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    def _session(self) -> contextlib.AbstractAsyncContextManager[AsyncSession]:
        return scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        )

    # ------------------------------------------------------------------ upload

    async def upload(self, upload: _Upload, raw_filename: str | None) -> ImportResultOut:
        if (upload.content_type or "").split(";")[0].strip().lower() not in ALLOWED_TYPES:
            raise ApiError("validation_error", "That file isn't a PDF.")
        limited = await RateLimiter(self.ctx.redis).throttle(
            f"import:{self.principal.user_id}", limit=UPLOADS_PER_MINUTE, window=60
        )
        if limited is not None:
            raise ApiError(
                "rate_limited", limited.message, headers={"Retry-After": str(limited.retry_after)}
            )
        store = self.ctx.settings.document_store / STORE_SUBDIR
        tmp, sha, size = await _stream_to_temp(upload, store)
        try:
            statement = await asyncio.to_thread(_parse_pdf, tmp)
            relative = f"{STORE_SUBDIR}/{sha}.pdf"  # derived from the hash only
            final = store / f"{sha}.pdf"
            placed = not final.exists()
            if placed:
                os.replace(tmp, final)
            result = None
            try:
                result = await self._import(statement, sha, relative, size, raw_filename)
            finally:
                if result is None and placed:
                    # Nothing references the file (the import rolled back): don't keep it.
                    final.unlink(missing_ok=True)
        finally:
            tmp.unlink(missing_ok=True)
        document_created, document_id, filename, blocks, balances_only = result
        await audit.record(
            self.ctx.factory,
            "import.upload",
            household_id=self.principal.household_id,
            user_id=self.principal.user_id,
            ip=self.ctx.ip,
            entity="document",
            entity_id=document_id,
            detail={
                "created": document_created,
                "blocks": len(blocks),
                "rows_inserted": sum(b.rows_inserted for b in blocks),
                "size_bytes": size,
            },
        )
        return ImportResultOut(
            document_id=document_id,
            filename=filename,
            created=document_created,
            balances_only=balances_only,
            blocks=blocks,
        )

    async def _import(
        self,
        statement: ParsedStatement,
        sha: str,
        relative: str,
        size: int,
        raw_filename: str | None,
    ) -> tuple[bool, uuid.UUID, str, list[ImportBlockOut], bool]:
        meta = DocumentMeta(
            sha256=sha,
            filename=sanitise_filename(raw_filename),
            storage_path=relative,
            size_bytes=size,
        )
        async with self._session() as s:
            existing = (
                await s.execute(select(Document.id).where(Document.sha256 == sha))
            ).scalar_one_or_none()
        try:
            result = await import_statement(
                self.ctx.factory,
                self.principal.household_id,
                statement,
                meta,
                person_id=self.principal.person_id,
            )
        except IntegrityError:
            # The same file was imported by another person in the household and is hidden
            # from this session: say nothing about it beyond "already there".
            raise ApiError("conflict", "That statement is already in this household.") from None
        except AccountNotVisibleError:
            raise ApiError("conflict", "That statement is already in this household.") from None
        async with self._session() as s:
            doc = (
                await s.execute(select(Document.filename).where(Document.id == result.document_id))
            ).scalar_one()
        blocks = await self._blocks(result.blocks)
        return existing is None, result.document_id, doc, blocks, result.balances_only

    async def _blocks(self, results: tuple[BlockResult, ...]) -> list[ImportBlockOut]:
        if not results:
            return []
        created = {r.run_id: r.created for r in results}
        stmt = (
            select(ImportRun, Account.name, Account.currency)
            .join(Account, Account.id == ImportRun.account_id)
            .where(ImportRun.id.in_(created))
        )
        async with self._session() as s:
            rows = {run.id: (run, name, cur) for run, name, cur in (await s.execute(stmt)).all()}
        out: list[ImportBlockOut] = []
        for r in results:
            run, name, cur = rows[r.run_id]
            out.append(
                ImportBlockOut.model_validate(
                    {
                        "run_id": run.id,
                        "account_id": r.account_id,
                        "account_name": name,
                        "currency": cur,
                        "reconcile_status": run.reconcile_status,
                        "period_start": run.period_start,
                        "period_end": run.period_end,
                        "opening_balance": run.opening_balance,
                        "closing_balance": run.closing_balance,
                        "txn_sum": run.txn_sum,
                        "balance_diff": run.balance_diff,
                        "rows_parsed": run.rows_parsed,
                        "rows_inserted": r.rows_inserted,
                        "rows_duplicate": r.rows_duplicate,
                        "created": r.created,
                    }
                )
            )
        return out

    # ------------------------------------------------------------------ runs

    @staticmethod
    def _out(
        run: ImportRun, filename: str, account_name: str | None, cur: str | None
    ) -> ImportRunOut:
        return ImportRunOut.model_validate(
            {
                "id": run.id,
                "document_id": run.document_id,
                "filename": filename,
                "account_id": run.account_id,
                "account_name": account_name,
                "currency": cur,
                "status": run.status,
                "reconcile_status": run.reconcile_status,
                "period_start": run.period_start,
                "period_end": run.period_end,
                "opening_balance": run.opening_balance,
                "closing_balance": run.closing_balance,
                "txn_sum": run.txn_sum,
                "balance_diff": run.balance_diff,
                "rows_parsed": run.rows_parsed,
                "rows_inserted": run.rows_inserted,
                "rows_duplicate": run.rows_duplicate,
                "error_class": run.error_class,
                "created_at": run.created_at,
                "reviewed_at": run.reviewed_at,
            }
        )

    async def runs(self, *, limit: int, cursor: str | None) -> ImportRunPage:
        stmt = (
            select(ImportRun, Document.filename, Account.name, Account.currency)
            .join(Document, Document.id == ImportRun.document_id)
            .outerjoin(Account, Account.id == ImportRun.account_id)
        )
        if cursor:
            at, rid = _decode_cursor(cursor)
            stmt = stmt.where(
                (ImportRun.created_at < at) | ((ImportRun.created_at == at) & (ImportRun.id < rid))
            )
        stmt = stmt.order_by(ImportRun.created_at.desc(), ImportRun.id.desc()).limit(limit + 1)
        async with self._session() as s:
            rows = (await s.execute(stmt)).all()
        page = rows[:limit]
        return ImportRunPage(
            items=[self._out(*r) for r in page],
            next_cursor=_encode_cursor(page[-1][0].created_at, page[-1][0].id)
            if len(rows) > limit and page
            else None,
        )

    async def _one(self, s: AsyncSession, run_id: uuid.UUID) -> ImportRunOut | None:
        row = (
            await s.execute(
                select(ImportRun, Document.filename, Account.name, Account.currency)
                .join(Document, Document.id == ImportRun.document_id)
                .outerjoin(Account, Account.id == ImportRun.account_id)
                .where(ImportRun.id == run_id)
            )
        ).one_or_none()
        return None if row is None else self._out(*row)

    async def accept(self, run_id: uuid.UUID) -> ImportRunOut:
        """needs_review -> accepted by the session user. Never touches the numbers, so a run that
        didn't reconcile stays visibly unreconciled (``balance_diff`` is unchanged).
        Idempotent: accepting an accepted run returns it and writes nothing."""
        now = datetime.now(UTC)
        async with self._session() as s:
            current = await self._one(s, run_id)
            if current is None:
                raise ApiError("not_found", "That import run doesn't exist.")
            if current.reconcile_status == "accepted":
                return current
            if current.status != "succeeded" or current.reconcile_status != "needs_review":
                raise ApiError("conflict", "Only a run that needs review can be accepted.")
            moved = (
                await s.execute(
                    update(ImportRun)
                    .where(
                        ImportRun.id == run_id,
                        ImportRun.reconcile_status == "needs_review",
                        ImportRun.status == "succeeded",
                    )
                    .values(
                        reconcile_status="accepted",
                        reviewed_by_user_id=self.principal.user_id,
                        reviewed_at=now,
                    )
                    .returning(ImportRun.id)
                    .execution_options(synchronize_session=False)
                )
            ).scalar_one_or_none()
            if moved is None:
                # Visible but not writable (another person's statement), or accepted meanwhile.
                again = await self._one(s, run_id)
                if again is not None and again.reconcile_status == "accepted":
                    return again
                raise ApiError("forbidden", "You can't accept this import run.")
            audit.add(
                s,
                "import.accept",
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
                entity="import_run",
                entity_id=run_id,
                detail={"document_id": str(current.document_id)},
            )
            await s.flush()
            s.expire_all()
            done = await self._one(s, run_id)
        if done is None:
            raise ApiError("not_found", "That import run doesn't exist.")
        return done


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "ImportService", "sanitise_filename"]
