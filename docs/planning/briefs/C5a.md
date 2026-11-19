# C5a Recurring detection, pure core — Anvil (Codex) — size S
Goal: detect recurring series (bills, subscriptions, paychecks) from transaction views. Pure functions only. The planted-bill eval comes later in C5/C7.
Read: docs/planning/roundtable/04-ledger-domain.md lines 1–16 and 40–45; backend/app/domain/money.py; ADR 0011; `.claude/rules/money.md`; your own C4a `TxnView` if it's merged (otherwise define a compatible one in `recurring.py`).
Do:
1. `backend/app/domain/recurring.py`: `detect_recurring(txns: Sequence[TxnView], as_of: date) -> list[RecurringSeries]`. Group by normalized merchant (lowercase, strip digits/store numbers/punctuation) and sign. Cadences: weekly, biweekly, semi-monthly, monthly, quarterly, annual. A series needs ≥ 3 occurrences (≥ 2 for annual). Its interval coefficient of variation must stay under a named threshold constant.
2. `RecurringSeries`: merchant key, cadence, typical amount (median, as a Decimal), amount range, `is_variable` (CV of amount above a threshold), next expected date, status `active | late | ended` relative to `as_of`, and `kind` `bill | subscription | income` (income = positive sign).
3. Tests: Hypothesis-generated series at each cadence with jitter ±2 days are recovered. Random noise yields no series. A variable utility bill is still detected with `is_variable=True`. A cancelled series turns `ended`. A biweekly paycheck is detected as income. Everything is deterministic.
Verify (no DB needed): `cd backend && uv run pytest -q tests/domain && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run lint-imports`.
Branch `feat/C5a-recurring-core` on your fork. Trailer `Co-Authored-By: Anvil (Holdfast) <agents@holdfastfinance.app>`. PR body ends `— Anvil, Holdfast`. Reviewer: Ledger. Don't merge.
