---
paths:
  - "backend/tests/**"
  - "web/src/**/*.test.{ts,tsx}"
  - "web/e2e/**"
---
# Testing

- pytest + pytest-asyncio for the backend, against a real Postgres (testcontainers or the compose `db` service), not SQLite. RLS and NUMERIC behavior must be real.
- Network is blocked in tests (socket guard in `conftest.py`). Plaid/SimpleFIN/LLM calls use recorded, sanitized JSON fixtures or fakes.
- Fixtures are synthetic: fake people, fake institutions ("First Example Bank"), and account masks like `0000`. Never real statements, credit reports, tax forms or names.
- Name tests `test_<behavior>.py` and assert behavior, not implementation. Every bug fix gets a regression test first.
- Frontend: vitest + Testing Library for components, Playwright for the 375px mobile and desktop smoke flows.
- Money tests compare `Decimal` to `Decimal("…")`, never to floats.
- Report exact commands and results (pass, fail, skip counts) in phase reports.
