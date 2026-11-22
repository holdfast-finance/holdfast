# 0031. Dependencies that had no ADR: pdfplumber, python-multipart, reportlab, hypothesis, uqr

- Status: Accepted
- Date: 2026-11-21
- Deciders: Keel; Beacon (record)

## Context
Five libraries entered the tree without an ADR naming them. They are libraries, not external
services, so rule 9 does not require one. The security rule still asks for a maintenance and
licence check on every new dependency, and this records it.

Licences below come from the installed package metadata (`uv run python -c "import importlib.metadata ..."`
in `backend/`, versions from `backend/uv.lock`). The AGPL-3.0 project can use all of them: MIT, BSD and
Apache-2.0 are permissive, and MPL-2.0 is file-level copyleft that is compatible with the AGPL and applies only
if we modify the library's own files.

## Decision
| Library | Where | Why | Licence (as installed) |
|---|---|---|---|
| `pdfplumber` 0.11.10 | runtime (`backend/pyproject.toml`) and dev | Reads text and positions from bank statement PDFs in the api and worker (`app/importers/pdf_text.py`). It is a runtime dependency, not optional. | MIT (classifier) |
| `python-multipart` 0.0.34 | runtime | FastAPI needs it to parse the multipart statement upload (`POST /api/imports/statements`, ADR 0020). | Apache-2.0 |
| `reportlab` 5.1.0 | dev only | Builds synthetic statement PDFs for tests and the sanitizer. It never ships in the runtime image. | BSD (classifier) |
| `hypothesis` 6.170.15 | dev only | Property tests for money, parsing and matching code. | MPL-2.0 |
| `uqr` 0.1.3 | web runtime (`web/package.json`) | Draws QR codes in the browser (`web/src/components/QrCode.tsx`), so no QR request leaves the machine. | Unverified: `web/node_modules` was not installed when this was written. Check `pnpm licenses list` before the first release. |

Maintenance: the versions above are the ones locked today. We did not check upstream release history
from this environment, so "actively maintained" is unverified for all five. `pip-audit` and `pnpm audit`
run in CI and cover known advisories.

## Consequences
- Dev-only libraries stay out of the runtime image. A test or lint should keep `reportlab` and `hypothesis` in the dev group.
- If the `uqr` check finds a licence that is not AGPL-compatible, replace it before release.
- New dependencies get a line here or in their own ADR when they are added.
