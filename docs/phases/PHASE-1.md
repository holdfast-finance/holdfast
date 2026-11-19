# Phase 1: Auth, household, data model (+ RLS, per-person visibility)

- **Verdict: PASS** (2026-11-19)
- Branch: `phase/1-auth-data-model`, verified at `58a6dbd`
- Built by parallel subagents against `PHASE-1-contract.md` (data-model, web-auth, auth-api), then reviewed (security, money invariants, UI/brand), fixed by three more subagents (data-model-fixes, web-cleanup, auth-hardening), re-reviewed for security, and checked independently by the acceptance checker twice (before and after hardening).

## Summary
- **Data model:** all 31 PLAN §5 tables plus `household_setting`, `webauthn_credential`, `auth_session` and `totp_recovery_code` (35 total), 125 indexes including every FK, `household_id`, the transaction date/category/merchant indexes and a trigram GIN on raw descriptions. Money is `Numeric` everywhere (no floats), masks hold at most four digits, SSN-shaped values are rejected, and migrations round-trip up/down/up.
- **Row-level security, enforced in Postgres as `holdfast_app`:** 29 tables with RLS enabled and forced; household isolation (invisible, can't insert/update/delete, can't move rows across households) and per-person visibility (`private` / `summary_only` / `joint` / household-wide), with separate read and write policies so seeing a shared account never lets you rewrite it. `audit_log` is append-only with no FKs; `create_household` is a pinned-search-path SECURITY DEFINER (ADR 0008).
- **Money invariants in the database:** a deferred trigger keeps splits summing exactly to their transaction with the same sign; `transfer_group_id` implies `is_transfer`; magnitude and ratio CHECKs; `pending_external_id` ready for Plaid pending→posted in Phase 2.
- **Auth:** passkeys first (usernameless, `userVerification: required`), password + TOTP fallback with recovery codes, server-side sessions in Postgres, CSRF header, 5-minute step-up for sensitive changes, one-time setup token, household invites, and an audit trail (ADR 0010). Hardened per the final security review: atomic rate limits, body caps, narrowed proxy trust, session revocation on credential change.
- **Web:** first-run setup wizard, passkey sign-in, Household settings (people, invites) and Security settings (passkeys, TOTP, recovery codes, audit log), token-pure components, lazy routes, MSW mocks matching the API contract.
- **Tests:** 532 backend (real Postgres as the app role + Redis, no skips), 191 web, and a Playwright e2e of the full first-run flow on a CDP virtual authenticator. `make test` now uses its own `holdfast_test` database and Redis db 15, so it no longer wipes dev data.

## Acceptance (phase-acceptance-checker, independent run at `58a6dbd`)
| Criterion | Evidence | Status |
|---|---|---|
| Backend tests | `make test`: 532 passed, 0 skipped | PASS |
| Web tests | `make test`: 16 files, 191 passed | PASS |
| Lint + types | ruff, ruff format (96 files), mypy (59 files), eslint 0 warnings, tsc: clean | PASS |
| Port rules (hard rule 12) | `make ports`: ok for dev, base, prod | PASS |
| All tables exist | `test_every_contract_table_exists`; dev db at `0003_app_user_household`, missing 0 of 35 | PASS |
| All tables indexed | `test_key_indexes_exist`, `test_every_foreign_key_is_indexed`, `test_every_household_table_has_household_id_index`; 125 indexes incl. trigram GIN | PASS |
| Schema conventions | no floats / masks only / migration round-trip / single head: all pass | PASS |
| RLS isolation as `holdfast_app` | 252 RLS/schema/role/migration tests passed (fixture asserts `current_user = holdfast_app`); 29 tables RLS forced, 7 exceptions match the contract | PASS |
| Per-person visibility (§20.3) | `test_rls_person_visibility.py` all pass (private invisible, summary-only shows balances only, no cross-person writes or ownership steals) | PASS |
| Audit append-only, roles | `test_audit_rows_cannot_be_updated_or_deleted`, `test_app_has_dml_only` ×31, `test_app_cannot_run_ddl` ×7; app role has no superuser/BYPASSRLS | PASS |
| Household with 2 people + passkey login, end to end | Isolated stack (`APP_ENV=test`, `E2E_HOOKS=true`): `e2e/first-run.e2e.ts` passed — claim, passkey, second person, sign out, passkey sign-in. DB after: 1 household, 2 people, 1 owner, 1 passkey (`last_used_at` set), audit rows for each step | PASS |
| Same on the dev stack (read-only) | `needs_setup:false`; 1 household, 2 people, 1 user, 1 passkey | PASS |
| Test hooks gated | `/api/test/setup-token`: dev 404, e2e stack 200 | PASS |
| CSRF / auth contract | `/api/auth/me` 401; POST without `X-Holdfast-CSRF` 403 `csrf_failed`; password alone can't sign in (422) | PASS |

## Reviewer findings and dispositions
**Security reviewer (first pass, on data model)**
- Account takeover: one `FOR ALL` policy let anyone who could see a shared/summary account rewrite its owner or visibility. **Fixed:** separate RESTRICTIVE read/write policies + ownership trigger (ADR 0008).
- `audit_log` FK cascades bypassed RLS. **Fixed:** plain uuids, no FKs; app role loses DELETE on `household`.
- Agent output not person-scoped. **Fixed:** `person_id` on `agent_conversation` / `agent_finding` with RESTRICTIVE scoping.
- Masks accepted full card numbers; SSN-shaped values allowed. **Fixed** with CHECKs.

**Security reviewer (final pass, on auth) → all fixed with regression tests (ADR 0010 "Hardening")**
- M: rate limit was check-then-act, so concurrent requests could brute-force TOTP. **Fixed:** atomic Lua reservation.
- L1 passkey lockout DoS, L2 trusted proxies too wide, L3 household-wide guard too narrow, L4 invite email leak across households, L5 argon2 on the event loop, L6 body size and challenge minting, L7 legacy invite path token, L8 sessions surviving credential changes, L9 test-mode safety net. **All fixed.**

**Security re-review of the hardening (`3fe928f..58a6dbd`): no critical, high or medium.** Open lows:
1. Password login holds a DB connection while queued for argon2 (`services/auth.py:170-178`); a burst could briefly exhaust the pool (5+10). Fix: verify outside the session, or take the semaphore first.
2. IP-scoped limits (passkey login, setup, invites) depend on Caddy seeing real client addresses. If Docker's userland proxy or rootless networking masks them, all clients share one bucket. **Check after deploying:** `audit_log.ip` should show tailnet addresses, not `172.30.10.x`.
3. `/api/setup` is effectively unthrottled once setup is complete (refunded, one cheap query). Negligible.
4. Informational: an owner could probe whether another household holds an email via invite accept. Moot while an instance has one household.

**Money-invariants reviewer**
- Splits not enforced, transfer flags inconsistent, sign conventions undocumented. **Fixed** in the DB (deferred split-sum trigger, CHECKs, column comments).
- Phase 2 items carried forward (see STATUS "Carry into Phase 2").

**UI/brand reviewer**
- Focus management (TOTP → recovery codes, reauth dialog), overclaiming security copy, non-token colors, iOS zoom on invite field, sheet close button overlap, unannounced copy button. **All fixed** (`fb05213`, web-cleanup merge `bd8f52d`).

## ADRs this phase
- 0008: RLS and per-person visibility (read/write policy split, agent scope, audit without FKs)
- 0009: Dual brand — Holo for Holdfast Cloud, Direction 01 for self-host
- 0010: Auth sessions, identity lookup and ephemeral auth state (+ Hardening section)

## Blocked: what the owner must do
- Nothing blocks Phase 1.
- Still open from Phase 0: run `./scripts/dev.ps1 keys` and `./scripts/dev.ps1 up` once on a personal Windows machine.

## Known gaps / follow-ups
- Security lows 1–4 above (1 and 2 before the first real deployment).
- Pass `WEBAUTHN_ORIGIN` through `docker-compose.yml` for non-default origins.
- Rename the session cookie to `__Host-hf_session` (logs everyone out; touches the web contract).
- API types in `web/src/api/types.ts` are hand-written (`TODO(openapi)`); generate from OpenAPI.
- New env var `E2E_HOOKS` and error code `payload_too_large` (413): any e2e stack needs `E2E_HOOKS=true` with `APP_ENV=test`.
- Brand-theme plumbing (`data-brand`, Holo tokens) is the first task after Phase 1 (ADR 0009).

## How to demo
```sh
make up            # dev stack behind https://localhost
make migrate       # alembic as holdfast_owner
make health        # → {"status":"ok",...}
open https://localhost   # first run: claim with the setup token from the api log, add a passkey
                         # Settings → Household: add a person, create an invite
                         # Settings → Security: passkeys, TOTP + recovery codes, audit log
make test && make lint && make ports
cd web && BASE_URL=https://localhost:<port> pnpm exec playwright test   # on an isolated stack with APP_ENV=test, E2E_HOOKS=true
```
