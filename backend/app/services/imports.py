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
import hmac
import json
import os
import re
import secrets
import uuid
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn, Protocol

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db_context import scoped_session
from app.domain.file_kind import (
    FileKind,
    WideTextGuard,
    detect_file_kind,
    has_binary_bytes,
    is_wide_text,
)
from app.domain.ledger_rules import InvalidAmountError
from app.domain.statement_parse import (
    ParsedStatement,
    StatementParseError,
    detect_profile,
    parse_statement,
)
from app.importers.export_statement import csv_statement, ofx_statement
from app.importers.pdf_text import MAX_PDF_BYTES, pdf_to_text
from app.importers.profile_loader import load_csv_profiles, load_profiles
from app.models import Account, Document, ImportRun
from app.schemas.imports import (
    ImportAccountOut,
    ImportBalanceOut,
    ImportBlockOut,
    ImportResultOut,
    ImportRunOut,
    ImportRunPage,
)
from app.security.ratelimit import RateLimiter
from app.services import audit
from app.services.context import RequestContext
from app.services.errors import ApiError
from app.services.plaid_slots import assert_can_import
from app.services.sessions import Principal
from app.services.statement_import import (
    AccountNotVisibleError,
    AccountTarget,
    AccountTargetError,
    BlockResult,
    DocumentMeta,
    import_statement,
    stored_review_reason,
)

# The client's MIME type is only a coarse gate (browsers disagree on CSV and send nothing for
# OFX/QFX); what a file really is comes from its bytes (``detect_file_kind``).
ALLOWED_TYPES = frozenset(
    {
        "application/pdf",
        "application/x-pdf",
        "application/octet-stream",
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
        "text/plain",
        "application/x-ofx",
        "application/ofx",
        "application/vnd.intu.qfx",
        "",
    }
)
MAX_TEXT_BYTES = 10 * 1024 * 1024  # a CSV export is far smaller
MAX_OFX_BYTES = 5 * 1024 * 1024  # OFX is tag-heavy: bound the parser's work separately
EXTENSIONS: dict[FileKind, str] = {"pdf": "pdf", "csv": "csv", "ofx": "ofx"}
MIME_TYPES: dict[FileKind, str] = {
    "pdf": "application/pdf",
    "csv": "text/csv",
    "ofx": "application/x-ofx",
}
KIND_LABELS: dict[FileKind, str] = {"pdf": "PDF", "csv": "CSV", "ofx": "OFX"}
SEPARATE_ACCOUNT_NOTICE = (
    "This file went into its own new account. It is not matched to accounts from your "
    "statements, so it may repeat transactions you already imported from them."
)
TARGET_MESSAGES = {
    "joint_not_chosen": "Choose a joint account for this import, or sign in as a person.",
    "account_kind_mismatch": "That file is the other kind (bank or card) than the chosen account.",
    "target_needs_single_account": "That file has several accounts. Import it as a new account.",
}
OTHER_ACCOUNT_NOTICE = (
    "This exact file is already in Holdfast under a different account, so nothing was added "
    "to the account you chose."
)
UNSUPPORTED = "That file isn't a PDF, CSV or OFX/QFX export."
UPLOADS_PER_MINUTE = 10
CHUNK = 64 * 1024
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
STORE_SUBDIR = "statements"
FILENAME_MAX = 100
TEMP_PREFIX = ".upload-"
PARSE_SLOTS = 2  # concurrent form + PDF parses per process
SLOT_WAIT_SECONDS = 10
PARSE_TIMEOUT_SECONDS = 30
LOCK_SECONDS = 120
BALANCE_LIMIT = Decimal(10) ** 12  # NUMERIC(14,2): twelve integer digits
# A client that stops sending must not hold a parse slot. 120 s lets a 21 MiB body arrive at about
# 1.5 Mbit/s (slow mobile over a tailnet); a typical statement is under 1 MiB. Slower links retry.
FORM_READ_SECONDS = 120
# One in-flight upload per user, in Redis so it holds across API workers. The TTL covers the longest
# legitimate request (slot wait + body + parse) so a crashed worker can't lock a user out for long.
USER_LOCK_SECONDS = FORM_READ_SECONDS + PARSE_TIMEOUT_SECONDS + SLOT_WAIT_SECONDS + 20
_RELEASE = (
    "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) end return 0"
)
log = structlog.get_logger(__name__)
_FILENAME_OK = re.compile(r"[^A-Za-z0-9 ._()\-]")


@dataclass(frozen=True)
class UploadFields:
    """The form fields beside the file that say where a CSV/OFX goes (a PDF ignores them)."""

    account: str | None = None  # "new" or the uuid of an existing import account
    account_kind: str | None = None  # "bank" | "card", for a new CSV account
    joint: str | None = None  # truthy: a joint (household) account rather than the uploader's


class _Upload(Protocol):
    """The part of Starlette's UploadFile used here (also what the tests pass)."""

    @property
    def content_type(self) -> str | None: ...

    async def read(self, size: int = -1, /) -> bytes: ...


def sanitise_filename(raw: str | None, ext: str = "pdf") -> str:
    """A display label: base name only, safe characters, bounded, always ``.<ext>``."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _FILENAME_OK.sub("_", name).strip(" .")
    name = re.sub(r"_{2,}", "_", name)
    suffix = f".{ext}"
    # The bytes decided the kind, so a mismatched name ("a.pdf" holding a CSV) loses its extension.
    stem = name
    for known in (".pdf", ".csv", ".ofx", ".qfx"):
        if name.lower().endswith(known):
            stem = name[: -len(known)]
    stem = stem.strip(" ._")[: FILENAME_MAX - len(suffix)]
    return f"{stem or 'statement'}{suffix}"


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
        return parsed.astimezone(UTC), uuid.UUID(run_id)
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError, OverflowError):
        raise ApiError("validation_error", "That page cursor isn't valid.") from None


def _parse_pdf(path: Path) -> ParsedStatement:
    text = pdf_to_text(path)
    profile = detect_profile(text, load_profiles())
    if profile is None:
        raise ApiError(
            "validation_error", "Holdfast doesn't have a profile for this statement yet."
        )
    return parse_statement(text, profile)


def _parse_file(
    path: Path, kind: FileKind, id_key: bytes | None, account_kind: str = "bank"
) -> ParsedStatement:
    """CPU-bound: run in a thread. Any failure becomes one fixed, content-free message.

    Parser messages can quote file content, so none is ever passed on, logged or returned.
    """
    label = KIND_LABELS[kind]
    try:
        if kind == "pdf":
            return _parse_pdf(path)
        data = path.read_bytes()
        if kind == "ofx":
            if id_key is None:
                raise ApiError("server_error", "The statement couldn't be saved.")
            return ofx_statement(data, id_key)
        statement = csv_statement(data, load_csv_profiles(), account_kind)
        if statement is None:
            raise ApiError("validation_error", "Holdfast doesn't have a profile for this CSV yet.")
        return statement
    except ApiError:
        raise
    except (StatementParseError, ValueError):
        what = "statement" if kind == "pdf" else label
        raise ApiError("validation_error", f"That {what} couldn't be read.") from None
    except Exception:  # pdfplumber / pdfminer / xml raise many types on malformed files
        raise ApiError("validation_error", f"That file isn't a readable {label}.") from None


def household_id_key(settings: Any, household_id: uuid.UUID) -> bytes | None:
    """Key for the OFX id tokens (FITIDs, account ids), derived per household.

    It comes from ``IMPORT_ID_KEY``, a dedicated secret that is never rotated: ``FERNET_KEY`` is
    a rotating ring, and rotating it would change every token and make re-exports look new.
    ``None`` when it is not set (an older ``.env``: ``make keys`` adds it to a new one).
    """
    secret = settings.import_id_key
    if secret is None:
        return None
    return hmac.new(
        secret.get_secret_value().encode(), b"ofx-fitid:" + household_id.bytes, hashlib.sha256
    ).digest()


async def _stream_to_temp(upload: _Upload, directory: Path) -> tuple[Path, str, int, FileKind]:
    """Copy the upload to a private temp file: (path, sha256 hex, size, kind). Hard-capped.

    The kind comes from the first chunk's bytes. A text file is checked chunk by chunk for
    binary bytes, so nothing binary gets through as CSV or OFX.
    """
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True, mode=0o700)
    tmp = directory / f"{TEMP_PREFIX}{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    kind: FileKind | None = None
    guard: WideTextGuard | None = None
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(CHUNK):
                if kind is None:
                    kind = detect_file_kind(chunk)
                    if kind is None:
                        raise UploadRefused("unsupported_file", "validation_error", UNSUPPORTED)
                    if is_wide_text(chunk):
                        guard = WideTextGuard(chunk)
                if kind != "pdf":
                    # UTF-16/32 legitimately holds NUL bytes: check its decoded text instead.
                    clean = guard.feed(chunk) if guard else not has_binary_bytes(chunk)
                    if not clean:
                        raise UploadRefused("unsupported_file", "validation_error", UNSUPPORTED)
                size += len(chunk)
                limit = {"pdf": MAX_PDF_BYTES, "csv": MAX_TEXT_BYTES, "ofx": MAX_OFX_BYTES}[kind]
                if size > limit:
                    raise UploadRefused(
                        "too_large",
                        "payload_too_large",
                        f"That {KIND_LABELS[kind]} is over the {limit // (1024 * 1024)} MiB limit.",
                    )
                digest.update(chunk)
                out.write(chunk)
    except BaseException:
        tmp.unlink(missing_ok=True)  # a temp name, never a hash-named stored file
        raise
    if size == 0 or kind is None:
        tmp.unlink(missing_ok=True)
        raise UploadRefused("empty", "validation_error", "That file is empty.")
    if guard is not None and not guard.finish():
        tmp.unlink(missing_ok=True)
        raise UploadRefused("unsupported_file", "validation_error", UNSUPPORTED)
    # The extractor picks its reader by suffix, so the temp file takes the detected kind's.
    typed = tmp.with_suffix(f".{EXTENSIONS[kind]}")
    try:
        os.replace(tmp, typed)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return typed, digest.hexdigest(), size, kind


class UploadRefused(ApiError):
    """An upload turned away; ``reason`` is a fixed code for the audit log (never content)."""

    def __init__(self, reason: str, code: str, message: str, **kw: Any) -> None:
        super().__init__(code, message, **kw)
        self.reason = reason


class ParseSlot:
    """One of ``PARSE_SLOTS`` per process: bounds concurrent form parsing and PDF parsing.

    A parse that times out can't be cancelled (it runs in a thread), so its slot is released when
    the thread really ends (``release_when``), not when the request gives up.
    """

    def __init__(self, sem: asyncio.Semaphore) -> None:
        self._sem = sem
        self._deferred = False

    async def __aenter__(self) -> ParseSlot:
        try:
            async with asyncio.timeout(SLOT_WAIT_SECONDS):
                await self._sem.acquire()
        except TimeoutError:
            raise ApiError(
                "rate_limited",
                "Holdfast is busy reading other statements. Try again shortly.",
                headers={"Retry-After": str(SLOT_WAIT_SECONDS)},
            ) from None
        return self

    def release_when(self, task: asyncio.Future[Any]) -> None:
        self._deferred = True
        task.add_done_callback(lambda _t: self._sem.release())

    async def __aexit__(self, *exc: object) -> None:
        if not self._deferred:
            self._sem.release()


_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def parse_slot() -> ParseSlot:
    loop = asyncio.get_running_loop()
    return ParseSlot(_slots.setdefault(loop, asyncio.Semaphore(PARSE_SLOTS)))


def _is_duplicate_document(exc: IntegrityError) -> bool:
    """SQLSTATE 23505 (unique violation) on the document table, and nothing else."""
    diag = getattr(exc.orig, "diag", None)
    return (
        getattr(exc.orig, "sqlstate", None) == "23505"
        and getattr(diag, "table_name", None) == "document"
    )


class ImportService:
    def __init__(self, ctx: RequestContext, principal: Principal) -> None:
        self.ctx = ctx
        self.principal = principal

    def _session(self) -> contextlib.AbstractAsyncContextManager[AsyncSession]:
        return scoped_session(
            self.ctx.factory, self.principal.household_id, self.principal.person_id
        )

    # ------------------------------------------------------------------ guards

    async def throttle(self) -> None:
        """Per-user upload limit. Called before the request body is read."""
        limited = await RateLimiter(self.ctx.redis).throttle(
            f"import:{self.principal.user_id}", limit=UPLOADS_PER_MINUTE, window=60
        )
        if limited is not None:
            await self.refuse(
                "rate_limited",
                "rate_limited",
                limited.message,
                headers={"Retry-After": str(limited.retry_after)},
            )

    async def release(self, key: str, token: str) -> None:
        """Delete ``key`` only if it still holds ``token`` (never someone else's lock)."""
        await self.ctx.redis.eval(_RELEASE, 1, key, token)  # type: ignore[misc]

    async def begin(self) -> str:
        """Claim the user's single upload slot, or refuse. Returns the token to release with."""
        token = secrets.token_hex(16)
        key = f"hf:import:user:{self.principal.user_id}"
        if not await self.ctx.redis.set(key, token, nx=True, ex=USER_LOCK_SECONDS):
            await self.refuse(
                "upload_in_flight",
                "rate_limited",
                "Another upload of yours is still running. Wait for it to finish.",
                headers={"Retry-After": "5"},
            )
        return token

    async def end(self, token: str) -> None:
        await self.release(f"hf:import:user:{self.principal.user_id}", token)

    async def refuse(
        self, reason: str, code: str, message: str, headers: dict[str, str] | None = None
    ) -> NoReturn:
        """Audit (reason code only, at most one row per user and reason a minute), then raise."""
        key = f"hf:import:refused:{self.principal.user_id}:{reason}"
        if await self.ctx.redis.set(key, "1", nx=True, ex=60):
            await audit.record(
                self.ctx.factory,
                "import.upload_refused",
                household_id=self.principal.household_id,
                user_id=self.principal.user_id,
                ip=self.ctx.ip,
                outcome="failure",
                detail={"reason": reason},
            )
        raise UploadRefused(reason, code, message, headers=headers)

    # ------------------------------------------------------------------ upload

    async def upload(
        self,
        upload: _Upload,
        raw_filename: str | None,
        slot: ParseSlot,
        fields: UploadFields | None = None,
    ) -> ImportResultOut:
        try:
            return await self._upload(upload, raw_filename, slot, fields or UploadFields())
        except UploadRefused as exc:
            await self.refuse(exc.reason, exc.code, exc.message, exc.headers)

    def _target(self, kind: FileKind, fields: UploadFields, sha: str) -> AccountTarget | None:
        """Where a CSV/OFX goes. A PDF has none: its account follows its profile."""
        if kind == "pdf":
            return None
        choice = (fields.account or "").strip().lower()
        if not choice:
            raise UploadRefused(
                "account_required",
                "validation_error",
                "Choose which account this file belongs to, or a new account.",
            )
        account_id: uuid.UUID | None = None
        if choice != "new":
            try:
                account_id = uuid.UUID(choice)
            except ValueError:
                raise UploadRefused(
                    "bad_account", "validation_error", "That account choice isn't valid."
                ) from None
        if kind == "csv" and fields.account_kind not in ("bank", "card"):
            raise UploadRefused(
                "account_kind_required",
                "validation_error",
                "Say whether this CSV is a bank account or a card.",
            )
        joint = (fields.joint or "").strip().lower() in ("1", "true", "yes", "on")
        if self.principal.person_id is None and not joint and account_id is None:
            raise UploadRefused(
                "joint_not_chosen", "validation_error", TARGET_MESSAGES["joint_not_chosen"]
            )
        return AccountTarget(
            account_id=account_id,
            joint=joint and account_id is None,
            new_token=sha[:16] if kind == "csv" else None,
            kind_from_file=kind == "ofx",
            declared_kind=fields.account_kind if kind == "csv" else None,
        )

    async def _upload(
        self, upload: _Upload, raw_filename: str | None, slot: ParseSlot, fields: UploadFields
    ) -> ImportResultOut:
        if (upload.content_type or "").split(";")[0].strip().lower() not in ALLOWED_TYPES:
            raise UploadRefused(
                "bad_content_type", "validation_error", "That file type can't be imported."
            )
        # Temp files and stored originals share one filesystem so the final rename is atomic.
        store = self.ctx.settings.document_store / STORE_SUBDIR
        tmp, sha, size, kind = await _stream_to_temp(upload, store)
        ext = EXTENSIONS[kind]
        lock_key = f"hf:import:lock:{self.principal.household_id}:{sha}"
        token = secrets.token_hex(16)
        locked = False
        try:
            locked = bool(await self.ctx.redis.set(lock_key, token, nx=True, ex=LOCK_SECONDS))
            if not locked:
                raise UploadRefused(
                    "in_progress", "conflict", "That statement is already being imported."
                )
            target = self._target(kind, fields, sha)
            statement = await self._parse(tmp, slot, kind, fields.account_kind or "bank")
            household_dir = store / str(self.principal.household_id)
            final = household_dir / f"{sha}.{ext}"
            # Under the household's own directory, named by hash: nothing is shared across
            # households and no client text reaches the path.
            relative = f"{STORE_SUBDIR}/{self.principal.household_id}/{sha}.{ext}"
            await asyncio.to_thread(household_dir.mkdir, exist_ok=True, mode=0o700)
            if final.exists():
                os.utime(final)  # in use again: a later sweep must not see an old orphan
            else:
                os.replace(tmp, final)
            # A failed import leaves the stored file: it is named by hash, and another request
            # may be using it. ``sweep_store`` removes orphans later.
            result = await self._import(statement, sha, relative, size, raw_filename, kind, target)
        finally:
            tmp.unlink(missing_ok=True)
            if locked:
                await self.release(lock_key, token)
        document_created, document_id, filename, blocks, balances_only, notice = result
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
                "kind": kind,
            },
        )
        return ImportResultOut(
            document_id=document_id,
            filename=filename,
            file_kind=kind,
            notice=notice,
            created=document_created,
            balances_only=balances_only,
            blocks=blocks,
            balances=[
                ImportBalanceOut(
                    account_last4=b.account_last4,
                    opening_balance=b.opening_balance,
                    closing_balance=b.closing_balance,
                )
                for b in statement.blocks
            ]
            if balances_only
            else [],
        )

    async def _parse(
        self, path: Path, slot: ParseSlot, kind: FileKind, account_kind: str
    ) -> ParsedStatement:
        id_key = household_id_key(self.ctx.settings, self.principal.household_id)
        if kind == "ofx" and id_key is None:
            raise UploadRefused(
                "id_key_missing", "server_error", "The statement couldn't be saved."
            )
        task = asyncio.ensure_future(
            asyncio.to_thread(_parse_file, path, kind, id_key, account_kind)
        )
        try:
            done, _ = await asyncio.wait({task}, timeout=PARSE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            slot.release_when(task)  # the thread keeps running: it keeps the slot
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            raise
        if not done:
            # The thread can't be interrupted: keep its slot until it ends, answer now.
            slot.release_when(task)
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            raise UploadRefused(
                "parse_timeout", "validation_error", "That statement took too long to read."
            )
        try:
            return task.result()
        except UploadRefused:
            raise
        except ApiError as exc:
            raise UploadRefused("unreadable", exc.code, exc.message) from None

    async def _import(
        self,
        statement: ParsedStatement,
        sha: str,
        relative: str,
        size: int,
        raw_filename: str | None,
        kind: FileKind,
        target: AccountTarget | None = None,
    ) -> tuple[bool, uuid.UUID, str, list[ImportBlockOut], bool, str | None]:
        meta = DocumentMeta(
            sha256=sha,
            filename=sanitise_filename(raw_filename, EXTENSIONS[kind]),
            storage_path=relative,
            size_bytes=size,
            mime=MIME_TYPES[kind],
        )
        for block in statement.blocks:
            for value in (block.opening_balance, block.closing_balance):
                if value is not None and abs(value) >= BALANCE_LIMIT:
                    raise UploadRefused(
                        "amount_out_of_range",
                        "validation_error",
                        "An amount in that statement is out of range.",
                    )
        async with self._session() as s:
            # ADR 0033 section 7: no real statement beside a sandbox connection.
            try:
                await assert_can_import(s, self.principal.household_id)
            except ApiError as exc:
                raise UploadRefused(exc.code, exc.code, exc.message) from None
            existing = (
                await s.execute(select(Document.id).where(Document.sha256 == sha))
            ).scalar_one_or_none()
            known_accounts = set(
                (await s.execute(select(Account.id).where(Account.statement_key.is_not(None))))
                .scalars()
                .all()
            )
        try:
            result = await import_statement(
                self.ctx.factory,
                self.principal.household_id,
                statement,
                meta,
                person_id=self.principal.person_id,
                target=target,
            )
        except AccountTargetError as exc:
            raise UploadRefused(exc.reason, "conflict", TARGET_MESSAGES[exc.reason]) from None
        except IntegrityError as exc:
            if _is_duplicate_document(exc):
                # Imported at the same moment, or by someone whose copy this session can't see.
                raise UploadRefused(
                    "duplicate", "conflict", "That statement was already imported."
                ) from None
            log.error("import.db_error", error_class=type(exc.orig or exc).__name__)
            raise UploadRefused(
                "db_error", "server_error", "The statement couldn't be saved."
            ) from None
        except InvalidAmountError:
            raise UploadRefused(
                "amount_out_of_range",
                "validation_error",
                "An amount in that statement is out of range.",
            ) from None
        except AccountNotVisibleError:
            raise UploadRefused(
                "account_not_visible",
                "conflict",
                "That statement's account belongs to another person in this household.",
            ) from None
        except DBAPIError as exc:
            log.error("import.db_error", error_class=type(exc.orig or exc).__name__)
            raise UploadRefused(
                "db_error", "server_error", "The statement couldn't be saved."
            ) from None
        async with self._session() as s:
            doc = (
                await s.execute(select(Document.filename).where(Document.id == result.document_id))
            ).scalar_one()
        blocks = await self._blocks(result.blocks)
        notice = None
        # A new account made by a CSV/OFX while others exist: accounts are keyed by import
        # profile, so the file is not matched to a statement's account (no cross-source dedupe).
        new_account = any(r.created and r.account_id not in known_accounts for r in result.blocks)
        if kind != "pdf" and known_accounts and new_account:
            notice = SEPARATE_ACCOUNT_NOTICE
        if result.in_other_account:
            notice = OTHER_ACCOUNT_NOTICE
        return existing is None, result.document_id, doc, blocks, result.balances_only, notice

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
                        "rows_overlap": r.rows_overlap,
                        "created": r.created,
                        "reason": (r.reason or stored_review_reason(run))
                        if run.reconcile_status == "needs_review"
                        else None,
                    }
                )
            )
        return out

    async def accounts(self) -> list[ImportAccountOut]:
        stmt = (
            select(Account)
            # Every account a statement can land in: imported, or adopted by a connection
            # (ADR 0033 §4 keeps its statement key).
            .where(Account.statement_key.is_not(None), Account.closed_at.is_(None))
            .order_by(Account.name, Account.id)
        )
        async with self._session() as s:
            rows = (await s.execute(stmt)).scalars().all()
        return [
            ImportAccountOut(
                id=a.id,
                name=a.name,
                type="card" if a.type == "credit" else "bank",
                mask=a.mask,
                currency=a.currency,
                joint=a.owner_person_id is None,
            )
            for a in rows
            # Visible is not writable: another person's shared account can be read, not added to.
            if a.owner_person_id is None or a.owner_person_id == self.principal.person_id
        ]

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
                "reason": stored_review_reason(run),
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
