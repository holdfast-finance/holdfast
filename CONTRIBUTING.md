# Contributing to Holdfast

Thanks for helping. Holdfast handles people's most sensitive data, so the bar is high and the rules are strict.

## Ground rules
- Read `CLAUDE.md` (the project's engineering rules apply to humans too) and the relevant `docs/adr/`.
- **Never** commit real financial data: statements, credit reports, tax forms, exports, account numbers, tokens. Fixtures are synthetic or sanitized.
- Money is `Decimal`, never float. Outflow negative, inflow positive.
- Every change ships with tests.

## Setup
Requires uv, pnpm, Docker and gitleaks. After cloning:
```sh
git config core.hooksPath .githooks   # secret scan on commit
```
Commands are in `CLAUDE.md` → Commands (available from Phase 0).

## Commits
Conventional Commits (`feat(scope): …`, `fix(scope): …`, `docs: …`). Sign off every commit
(`git commit -s`) to certify the [Developer Certificate of Origin](https://developercertificate.org/).

## Pull requests
Explain the problem, the resulting behavior and how you validated it (exact commands and results). Include screenshots at 375px and desktop for UI changes. Keep PRs focused.
