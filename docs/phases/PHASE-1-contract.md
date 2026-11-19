# Phase 1: integration contract (auth, household, data model)

Spec: PLAN.md Section 5 (data model), 12 (auth), 13 Phase 1, 17.4, 20.3 (per-person visibility + RLS), and ADRs 0006/0007. This file is binding for all Phase 1 workstreams. Raise any deviation in your report.

**Acceptance (PLAN 13):** create a household with 2 people, log in with a passkey, all tables exist with indexes. In addition, RLS isolation between households and per-person visibility are proven by tests running as `holdfast_app`.

## Workstreams and ownership
| Workstream | Owns | Starts |
|---|---|---|
| **data-model** | `backend/app/models/**`, `backend/app/db.py`, `backend/app/db_context.py`, `backend/migrations/**`, `backend/tests/models/**`, `backend/tests/test_rls*.py` | now |
| **web-auth** | `web/**` | now (against this API contract, with mocks) |
| **auth-api** | `backend/app/api/**` (except routes_health), `backend/app/security/**`, `backend/app/schemas/**`, `backend/app/services/**`, `backend/tests/api/**`, `web/e2e/**` (Playwright) | after data-model merges |
Shared files (`pyproject.toml`, `uv.lock`, `app/config.py`, `app/main.py`): add only what you need, and say so in your report.

## Schema conventions
- PKs `id UUID DEFAULT gen_random_uuid()`. Timestamps are `timestamptz DEFAULT now()`, calendar dates are `date`.
- Money `NUMERIC(14,2)`. Share/crypto quantities `NUMERIC(28,10)`. Prices `NUMERIC(18,8)`. Never float.
- Enum-like columns are `text` + `CHECK (col IN (...))` (easier to evolve than PG enums). Valid values are listed in PLAN Section 5 comments.
- FKs `ON DELETE` explicit: household → children `CASCADE`, optional refs `SET NULL`.
- Indexes: every FK, plus the PLAN Section 5 "Key indexes" list (`transaction(account_id, posted_date)`, `transaction(category_id, posted_date)`, `transaction(merchant_name)`, trigram GIN on `description_raw`, `balance_snapshot(as_of_date)`), plus `household_id` on every scoped table.
- Section 5 tables to create **now**: household, person, app_user, institution, **bank_connection** (see below), account, transaction, transaction_split, category, rule, balance_snapshot, security, holding, investment_txn, liability, budget, budget_line, credit_report, tradeline, credit_inquiry, credit_score_entry, document, parsed_field, tax_year, tax_item, tax_scenario, estimate_snapshot, agent_conversation, agent_message, agent_finding, audit_log. **Not now:** crypto tables (Section 18.5, Phase 10a), the `budget_period_actual` materialized view (Phase 5), and `v_*` views (built with their features).
- **Deviation from PLAN 5:** `plaid_item` becomes **`bank_connection`** (Section 20.3 adds SimpleFIN behind a `BankConnector` interface): `provider` CHECK IN ('plaid','simplefin'), `external_id` (Plaid item_id / SimpleFIN id), `access_token_enc bytea`, `env`, `products text[]`, `status`, `error_code`, `consent_expires_at`, `tx_cursor`, `last_synced_at`, `last_success_at`, `last_error_at`. `account.bank_connection_id` replaces `plaid_item_id`. Unique `(provider, external_id)`.
- New for 17.4 (bring your own keys): `household_setting(household_id, key text, value_enc bytea, updated_at, PK(household_id,key))`.
- New for 20.3: `account.visibility text NOT NULL DEFAULT 'shared' CHECK IN ('private','shared','summary_only')`.
- `app_user`: `id, person_id UNIQUE NULL, email citext UNIQUE` (or lower() unique index), `password_hash text NULL` (argon2id), `totp_secret_enc bytea NULL`, `totp_enabled bool`, `role CHECK IN ('owner','member')`, `created_at`, `last_login_at`, `disabled_at`. WebAuthn creds go in their own table, not jsonb:
  - `webauthn_credential(id, user_id → app_user CASCADE, credential_id bytea UNIQUE, public_key bytea, sign_count bigint, transports text[], aaguid uuid NULL, backup_eligible bool, backup_state bool, nickname text, created_at, last_used_at)`
  - `auth_session(id, user_id CASCADE, token_hash bytea UNIQUE (sha256), created_at, expires_at, last_seen_at, reauth_at timestamptz NULL, ip inet NULL, user_agent text NULL, revoked_at NULL)`
  - `totp_recovery_code(id, user_id CASCADE, code_hash text, used_at NULL)`
- SSNs and full account numbers are **never** columns (masks only).

## Row-level security model (enforced in DB, runtime role `holdfast_app`)
- Per-request context: within each DB transaction the API runs `SELECT set_config('app.household_id', :hid, true), set_config('app.person_id', :pid, true)` (transaction-local). Provide `app/db_context.py`: an async helper/dependency `scoped_session(household_id, person_id)` that opens a session, begins a transaction and sets both GUCs. Background jobs use the same helper.
- Helper SQL functions (created in the migration, `STABLE`, owned by `holdfast_owner`): `app_household_id() → uuid` and `app_person_id() → uuid`, which read the GUCs with `current_setting(..., true)` and return NULL when unset (fail closed: NULL never matches).
- **Every household-scoped table:** `ENABLE` + `FORCE ROW LEVEL SECURITY`, policy `USING (household_id = app_household_id()) WITH CHECK (household_id = app_household_id())`. Child tables without their own `household_id` (e.g. `transaction_split`, `parsed_field`, `agent_message`, `budget_line`, `tradeline`) either get a denormalized `household_id` (preferred, simpler policies and indexes) or a policy via EXISTS on the parent. Choose per table and document it.
- **Per-person visibility (20.3):**
  - `account`: visible if household matches AND (`visibility IN ('shared','summary_only')` OR `owner_person_id IS NULL` (joint) OR `owner_person_id = app_person_id()`).
  - Detail tables (`transaction`, `transaction_split`, `holding`, `investment_txn`, `liability`) are visible only if their account is visible **and** not `summary_only`-for-someone-else, i.e. `visibility='shared'` OR joint OR owner is me.
  - `balance_snapshot` follows `account` visibility (summary-only viewers see balances, not transactions).
- **Identity tables** (`app_user`, `webauthn_credential`, `auth_session`, `totp_recovery_code`) are needed **before** a household context exists (login). No RLS on them. The app accesses them only through `app/services/auth*` code, with grants SELECT/INSERT/UPDATE/DELETE limited to these. `household` and `person` have RLS by `id = app_household_id()` / `household_id = app_household_id()`. First-run setup inserts the household via a `SECURITY DEFINER` function `create_household(name text) RETURNS uuid` owned by `holdfast_owner` (the only way to create one without context), with EXECUTE granted to `holdfast_app`.
- **`audit_log`:** `household_id NULL` (pre-auth events). The app role gets INSERT + SELECT only (no UPDATE/DELETE: append-only). SELECT policy: `household_id = app_household_id()`. INSERT is always allowed (WITH CHECK `household_id IS NULL OR household_id = app_household_id()`).
- Tests (as `holdfast_app`, real Postgres): household A can't see/insert/update B's rows in every scoped table (parametrized over the table list), unset context sees nothing, private accounts are invisible to the other person, summary_only shows the account and balances but not transactions, and audit_log rows can't be updated or deleted.

## Auth model
- Methods: **passkey** (WebAuthn, discoverable credentials / usernameless, `userVerification: required`) as primary. **Fallback:** email + password (argon2id) + **TOTP** (both required), with 10 single-use recovery codes. No password-only login.
- RP: `rp_id = APP_HOST` (default `localhost`), origin `https://{APP_HOST}` (setting `WEBAUTHN_ORIGIN` overrides). Library: `webauthn` (py_webauthn) on the backend, `@simplewebauthn/browser` on the web.
- Sessions: opaque 32-byte random token in cookie `hf_session` (`HttpOnly; Secure; SameSite=Strict; Path=/`), stored as sha256 in `auth_session`. 12h absolute lifetime. `reauth_at` is refreshed by a passkey/TOTP step-up, and sensitive actions require `reauth_at` within 5 minutes.
- CSRF: SameSite=Strict + every non-GET `/api/*` must carry `X-Holdfast-CSRF: 1` (the web client always sends it). Reject otherwise.
- Rate limiting (Redis): login/assertion attempts per IP and per account, with exponential lockout. Audit every attempt.
- First-run: if no household exists, `GET /api/setup/status` → `{"needs_setup": true}`. On startup the API logs a one-time **setup token** (random, stored hashed in Redis with a 1h TTL, regenerated on restart while setup is pending). `POST /api/setup` requires it, so a random device on the tailnet can't claim the instance.

## API contract (all JSON, all under `/api`, errors `{"error": {"code": "...", "message": "..."}}`)
| Method + path | Body → Response | Notes |
|---|---|---|
| GET `/setup/status` | → `{needs_setup: bool}` | public |
| POST `/setup` | `{setup_token, household_name, display_name, email}` → `{user, household}` + session cookie | creates household, person (filing_role 'primary'), owner user, then the client must register a passkey |
| POST `/auth/passkey/register/options` | `{nickname?}` → PublicKeyCredentialCreationOptionsJSON | requires session (setup or logged in) |
| POST `/auth/passkey/register/verify` | `{credential, nickname?}` → `{credential: {id, nickname, created_at}}` | |
| POST `/auth/passkey/login/options` | `{}` → PublicKeyCredentialRequestOptionsJSON | public, challenge stored in Redis (5 min) |
| POST `/auth/passkey/login/verify` | `{credential}` → `{user}` + cookie | |
| POST `/auth/password/login` | `{email, password, totp_code \| recovery_code}` → `{user}` + cookie | fallback |
| POST `/auth/logout` | → 204 | revokes session |
| GET `/auth/me` | → `{user: {id, email, role, person: {id, display_name, filing_role}}, household: {id, name}, reauth_fresh: bool}` | 401 if none |
| POST `/auth/reauth/passkey/options` · `/verify` | step-up | sets reauth_at |
| POST `/auth/totp/setup` → `{otpauth_uri, secret}` · POST `/auth/totp/enable` `{code}` → `{recovery_codes: [..10]}` · POST `/auth/totp/disable` (reauth) | | |
| POST `/auth/password` `{password}` (reauth) | sets/changes password | |
| GET/DELETE `/auth/passkeys[/{id}]` | list / remove (reauth, never the last credential unless TOTP is enabled) | |
| GET `/household` · PATCH `/household` `{name}` (owner) | | |
| GET `/household/people` · POST `{display_name, filing_role}` · PATCH/DELETE `/household/people/{id}` (owner) | person CRUD | filing_role ∈ primary, spouse, dependent |
| POST `/household/people/{id}/invite` (owner, reauth) | `{email}` → `{invite_url}` | one-time link (24h) that lets that person create their user and register a passkey |
| POST `/invites/accept` | `{token}` → session | then register passkey |
| GET `/audit?limit&before` (owner) | → `{items: [...]}` | |
User JSON never includes password_hash, TOTP secret or credential public keys.

## Web (web-auth workstream)
- Routes: `/setup` (first-run wizard: token → household + you → register passkey → optional "add a second person"), `/login` (big "Sign in with passkey" button + "Use password + code instead" link), `/invite/:token`, Settings → **Household** (people list, add, edit, invite) and **Security** (passkeys list/add/remove, TOTP enable with QR rendered locally (no external QR service), recovery codes shown once, password set, active sessions = Phase 9).
- Route guard: unauthenticated users go to `/login`, or `/setup` when `needs_setup`. Auth state from `GET /api/auth/me` via TanStack Query.
- API client: typed fetch wrapper that always sends `X-Holdfast-CSRF: 1`, credentials `same-origin`, and maps the error shape. MSW (Mock Service Worker) handlers mirror this contract for tests and `pnpm dev` without a backend (only when `VITE_MOCK_API=1`).
- Brand: Direction 01, AA text tokens, 44px targets, works at 375px.
