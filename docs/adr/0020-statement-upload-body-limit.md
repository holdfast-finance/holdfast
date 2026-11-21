# 0020. The statement upload route takes a 21 MiB body; every other route keeps 1 MiB

- Status: Accepted
- Date: 2026-11-21
- Deciders: Keel; Tide (I5b)
- Amends: ADR 0010 ("Body size")

## Context
ADR 0010 caps every `/api/*` body at 1 MiB, in Caddy (`request_body`) and again in the ASGI
middleware (`app/security/body_limit.py`). Bank statement PDFs are routinely larger than that:
a multi-account or scanned-in-part statement is several MiB. The importer already refuses
anything over `MAX_PDF_BYTES` (20 MiB, `app/importers/pdf_text.py`) because its row regexes are
quadratic on long lines, so a bigger body could never be imported anyway.

## Decision
- `POST /api/imports/statements` (exact method and path, nothing else) accepts a body of up to
  **21 MiB**: the 20 MiB file cap plus 1 MiB for multipart framing and the other form parts. The
  file limit is enforced separately and exactly (20 MiB, `413 payload_too_large`).
- The limit is raised in three places that must agree: `UPLOAD_MAX_BODY_BYTES` in the ASGI
  middleware, and `request_body { max_size 21MiB }` in a dedicated `handle @statement_upload`
  block in both `infra/caddy/Caddyfile` (dev) and `Caddyfile.prod`. All other routes stay at 1 MiB.
- The middleware keeps counting real bytes as they stream, so a lying or absent `Content-Length`
  is still cut off at the limit.
- **Memory and disk, as built.** Starlette's multipart parser spools each part to a temp file once it
  passes 1 MiB, in `/tmp`. The api container's `/tmp` is a tmpfs, so a spooled upload does sit in RAM
  (counted against the container's memory). It is bounded, not absent: at most **2** uploads are read
  at once per process (`PARSE_SLOTS`), each at most 21 MiB, so `/tmp` is sized `64m` and the api
  service gets `mem_limit: 1536m` (about 150 MB idle, two PDF parses at an estimated 500 MB each,
  the tmpfs, and headroom; an estimate to revisit with a measured peak). The route then copies the
  spooled file to a private temp file in the document store (disk) while hashing it with a hard byte
  counter, so a 20 MiB + 1 byte file is refused with 413 even under the body cap.
- **Order of checks** on `POST /api/imports/statements`: session, per-user rate limit (10 a minute,
  Redis), content type, then a parse slot, and only then the body is read. A rate-limited or
  anonymous request never has its body read. Refusals are audited (`import.upload_refused`, a fixed
  reason code, at most one row per user and reason a minute).
- **Slow bodies.** Uvicorn and Caddy have no body-read timeout, so the route enforces one: reading
  the form is wrapped in `asyncio.timeout(120)` (enough for a full 21 MiB body at about 1.5 Mbit/s;
  statements are normally under 1 MiB), and a body that stalls is refused with 422 and gives its
  slot back. Each user may also have only **one upload in flight**, a Redis `SET NX` with a token
  and a 180 s TTL, released by compare-and-delete in `finally` (Redis rather than an in-process
  counter so it holds across API workers). One person can therefore never hold both slots.
- **Stored originals** live at `statements/<household id>/<sha256>.pdf`: one directory per household,
  named by hash, never shared across households, never derived from client text. The request path
  never deletes a hash-named file (another request may be using it); a failed import leaves an
  orphan and `app.importers.store_sweep.sweep_store` removes orphans and stale `.upload-*` temp files
  older than an hour. Files stored earlier under the flat layout (`statements/<sha256>.pdf`) or by
  other scripts are outside the sweep, and every reader must use `document.storage_path`, which is
  authoritative.
- **Duplicates.** Two uploads of the same bytes at the same moment: one wins, the other gets 409
  (a Redis lock per household and hash, backed by the unique constraint, SQLSTATE 23505 on
  `document`). Any other database error is a fixed 500 `server_error`; only the exception class
  name is logged.

## Follow-up stories
- Wire `sweep_store` to a job (or startup).
- Parse in a killable subprocess with memory and CPU limits.
- Audit refusal counters (the audit log keeps one refusal row per user and reason a minute).
- A reason column on `import_run`, and storing balances-only statements (needs a migration).

## Accepted risks
- **Parser resource use.** PDF parsing runs in a worker thread with a 30 s wall-clock answer and at
  most 2 at a time per process. A thread cannot be killed, so a timed-out parse keeps running (and
  keeps its slot) until it ends, and a decompression bomb inside one page is bounded only by the
  container `mem_limit`. Follow-up story: parse in a killable subprocess with its own memory and CPU
  limits. Until then this is an accepted risk, limited to authenticated household members.
- **Two 409 oracles.** "Already imported" (a copy the caller cannot see, because another person
  uploaded it) and "that account belongs to another person in this household" tell a household member
  that something exists. Both stay inside one household, which already shares its documents list;
  nothing crosses households (that case is a fresh import, not an error).
- The body-limit middleware's 413 is answered before the route runs, so an oversize upload is not
  in the audit log.
- A `needs_review` reason code and the balances of a `balances_only` upload are returned in the
  upload response only; `import_run` has no reason column and a run needs an account, so neither
  appears in `GET /api/imports/runs`. Follow-up (needs a migration): a reason column, and storing
  balances-only statements as balance snapshots with a run.

## Alternatives considered
- 10 MiB: covers nearly every statement, but a limit below what the importer accepts creates a
  second number to keep in sync for no safety gain.
- Raising the global cap: gives every JSON endpoint a 21x larger attack surface for one route.
- Chunked or resumable upload: more moving parts than a personal-finance app needs.

## Consequences
- `web/src/api/imports.ts` `MAX_UPLOAD_BYTES` becomes 20 MiB (Prism).
- Anything that changes the importer's `MAX_PDF_BYTES` must change `UPLOAD_MAX_BODY_BYTES` and both
  Caddyfiles with it (a test pins the middleware value to `MAX_PDF_BYTES` + 1 MiB).
