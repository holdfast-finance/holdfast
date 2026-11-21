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
  is still cut off at the limit. The route never buffers the upload in memory: Starlette spools
  multipart parts to a temp file, and the route then streams that file to the document store while
  hashing it with a hard byte counter.
- Uploads are rate limited per user (10 per minute) because each one costs PDF parsing CPU.

## Alternatives considered
- 10 MiB: covers nearly every statement, but a limit below what the importer accepts creates a
  second number to keep in sync for no safety gain.
- Raising the global cap: gives every JSON endpoint a 21x larger attack surface for one route.
- Chunked or resumable upload: more moving parts than a personal-finance app needs.

## Consequences
- `web/src/api/imports.ts` `MAX_UPLOAD_BYTES` becomes 20 MiB (Prism).
- Anything that changes the importer's `MAX_PDF_BYTES` must change `UPLOAD_MAX_BODY_BYTES` and both
  Caddyfiles with it (a test pins the middleware value to `MAX_PDF_BYTES` + 1 MiB).
