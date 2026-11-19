# 0010. Auth sessions, identity lookup and ephemeral auth state

- Status: Accepted
- Date: 2026-11-19
- Deciders: project engineer (Claude Code, Phase 1 auth-api workstream); owner review at phase end

## Context
PLAN 12 and the Phase 1 contract fix the auth model: passkeys first, password + TOTP fallback, opaque cookie sessions, CSRF header, Redis rate limits, a first-run setup token and one-time invites. ADR 0008 puts every household table behind FORCE RLS and leaves identity tables (`app_user`, `webauthn_credential`, `auth_session`, `totp_recovery_code`) without RLS so login can run before a household is known. Building the API surfaced choices the contract leaves open.

1. Login needs the user's household to scope everything after it. `app_user` pointed only at `person`, and `person` has FORCE RLS keyed on the household, so an unscoped session can't follow the link. A SECURITY DEFINER lookup doesn't help: FORCE RLS binds the function owner too, and it doesn't know the household either.
2. WebAuthn challenges, invites, the setup token, TOTP replay markers and rate-limit counters are short-lived. They need a store.
3. Requests reach the API through Caddy, so the TCP peer is always the proxy. Per-IP rate limits keyed on the peer would lock out everyone at once.

## Decision
- **`app_user.household_id`** (migration 0003), NOT NULL, FK to `household` ON DELETE CASCADE, plus a composite FK `(person_id, household_id) -> person(id, household_id)` so it can't disagree with the person. Login reads it from the identity table, then opens a scoped session. The RLS-coverage schema test exempts identity tables explicitly. Their `household_id` is a lookup key, not tenant data.
- **Ephemeral auth state lives in Redis**, with no new tables. Everything is keyed by a sha256 digest or a random challenge, never by a token in clear:
  - `hf:setup:token`: sha256 of the setup token, 1h TTL, re-minted on every API start while setup is pending.
  - `hf:wa:{register|login|reauth}:{challenge}`: bound to the session id (or `login`), 5 min, GETDEL single use.
  - `hf:invite:{sha256}`: invite payload, 24h, GETDEL on accept. `hf:invite:person:{id}` lets a new invite replace the old one.
  - `hf:totp:used:{user}:{step}`: SET NX, 2 min, so a TOTP step is accepted only once.
  - `hf:rl:*`: failure counters and lock keys per IP and per account (sha256 of the email, whether or not the account exists).
  Losing Redis loses pending challenges, invites and lockouts, never accounts or sessions. Sessions stay in Postgres (`auth_session`, sha256 only) so revocation and audit survive restarts.
- **Client IP**: trust `X-Forwarded-For` only when the peer is in `TRUSTED_PROXIES` (default loopback plus the pinned compose `frontend` subnet `172.30.10.0/24`; see Hardening), and take the right-most hop that isn't a trusted proxy.
- **Setup is resumable**: it counts as complete only once the owner holds a credential (passkey or enabled TOTP). Until then the token holder can re-run `POST /setup` to get a fresh session for the same owner. Concurrent setups are serialized with `pg_advisory_xact_lock`.
- **Step-up (5 min) also gates adding a credential** once the user has one. Sessions minted from a setup token or invite start with `reauth_at = NULL`. Holding a link doesn't prove a credential.

## Alternatives considered
- SECURITY DEFINER `user_household(user_id)`: blocked by FORCE RLS on `person` (the owner is bound too) without a BYPASSRLS role, which ADR 0007 rules out.
- `invite` and `webauthn_challenge` tables: durable, but they need cleanup jobs and RLS decisions for pre-auth rows, and they buy nothing. Expiry is the whole lifecycle.
- uvicorn `--forwarded-allow-ips`: works, but it lives in the Dockerfile and compose, and it trusts the left-most hop. The app-level resolver is testable and takes the right-most untrusted hop.

## Consequences
- A future multi-household user (one login, several households) needs a join table. Phase 1 is one household per user.
- Running several API workers is fine: all shared state is in Redis or Postgres. Each start re-mints the setup token, so with several replicas only the last-logged token is valid.
- Follow-ups: pass `WEBAUTHN_ORIGIN` through `docker-compose.yml` when an operator needs a non-default (`TRUSTED_PROXIES` is passed through now). Rename the session cookie to `__Host-hf_session` (forces `Secure`, `Path=/`, no `Domain`); deferred because it logs everyone out and touches the web contract. Phase 9 "active sessions" can list `auth_session` rows directly. After the first real deployment, run `docs/ops/client-ip-check.md` once per host: per-IP limits are only as good as the peer address Caddy sees (Docker's userland proxy or rootless port forwarding can mask it as a gateway address).

## Hardening (Phase 1 review)
Fixes from the final Phase 1 security review, each with a regression test (`backend/tests/api/test_auth_hardening.py` unless noted):
- **Rate limits are atomic.** `RateLimiter.reserve()` is one Lua script: refuse if any scope is locked, else `INCR` every scope and set the exponential lock on any scope that reached its threshold. The attempt is counted *before* anything is verified, so N parallel requests can't all pass a check that ran before any of them failed. A reserved attempt stays a failure unless settled: `succeed()` clears account scopes and refunds the rest, `refund()` uncounts it when nothing was verified (setup already complete). Used by password login, TOTP and passkey step-up, passkey login, setup and invite accept.
- **Passkeys count per IP only.** A passkey can't be guessed, and an account-scope lock on passkey login/step-up let anyone who knows an email lock its owner out. Password and TOTP keep both scopes; a passkey login clears the account's password/TOTP lockout.
- **Trusted proxies narrowed.** The compose `frontend` network is pinned to `172.30.10.0/24` and `TRUSTED_PROXIES` defaults to loopback plus that subnet (no longer all of RFC 1918). Caddy overwrites `X-Forwarded-For` with `{remote_host}` on `/api`.
- **Invite email pre-check is household-scoped.** An email taken in another household isn't revealed at invite time; accepting such an invite fails with a generic `409 conflict` that names neither the reason nor the household.
- **argon2 runs off the event loop** (`asyncio.to_thread`) behind a semaphore of 4 (`passwords.MAX_CONCURRENT_HASHES`). Password login holds no DB connection while it waits for or runs argon2 (F9): it reads the user in one short session, verifies with none open, then opens a second session that re-reads the user `FOR UPDATE` and fails the attempt if the hash, TOTP state or secret, or disabled flag changed in between. Up to the second-factor verdict a right and a wrong password cost the same work: a recovery-code attempt always runs the finishing session (consuming only when the password passed), and an outdated hash is upgraded best-effort only after the session is minted (`tests/api/test_security_lows.py`).
- **Body size.** Caddy caps `/api/*` bodies at 1 MB (`request_body`); the API repeats it at 1 MiB in a pure ASGI middleware (`app/security/body_limit.py`) that answers `413 payload_too_large` (new error code). WebAuthn credential JSON is capped at 64 KiB / 32 top-level keys (422). `POST /auth/passkey/login/options` is throttled to 30 per minute per IP, since every call stores a challenge.
- **Legacy invite path.** The web app redirects `/invite/<token>` to `/invite#<token>` with `replace`, so the path token leaves the address bar and history.
- **Credential changes revoke other sessions.** Removing a passkey, turning TOTP off and setting a password revoke every other `auth_session` of the user; the current one stays.
- **Test hooks need two switches.** `/api/test/*` mounts only with `APP_ENV=test` *and* `E2E_HOOKS=true` (new env var); a loopback-peer check was rejected because e2e reaches the API through Caddy. The API logs a WARNING at startup whenever `APP_ENV=test`.
- **household_wide guard widened** (`tests/models/test_household_wide_guard.py`): scans `app/api`, `app/services`, `app/domain`, `app/security` with an explicit allowlist.
