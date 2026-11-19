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
- **Client IP**: trust `X-Forwarded-For` only when the peer is in `TRUSTED_PROXIES` (default loopback plus private ranges; the API port is never published), and take the right-most hop that isn't a trusted proxy.
- **Setup is resumable**: it counts as complete only once the owner holds a credential (passkey or enabled TOTP). Until then the token holder can re-run `POST /setup` to get a fresh session for the same owner. Concurrent setups are serialized with `pg_advisory_xact_lock`.
- **Step-up (5 min) also gates adding a credential** once the user has one. Sessions minted from a setup token or invite start with `reauth_at = NULL`. Holding a link doesn't prove a credential.

## Alternatives considered
- SECURITY DEFINER `user_household(user_id)`: blocked by FORCE RLS on `person` (the owner is bound too) without a BYPASSRLS role, which ADR 0007 rules out.
- `invite` and `webauthn_challenge` tables: durable, but they need cleanup jobs and RLS decisions for pre-auth rows, and they buy nothing. Expiry is the whole lifecycle.
- uvicorn `--forwarded-allow-ips`: works, but it lives in the Dockerfile and compose, and it trusts the left-most hop. The app-level resolver is testable and takes the right-most untrusted hop.

## Consequences
- A future multi-household user (one login, several households) needs a join table. Phase 1 is one household per user.
- Running several API workers is fine: all shared state is in Redis or Postgres. Each start re-mints the setup token, so with several replicas only the last-logged token is valid.
- Follow-ups: pass `WEBAUTHN_ORIGIN` and `TRUSTED_PROXIES` through `docker-compose.yml` when an operator needs non-defaults (the defaults fit compose today). Phase 9 "active sessions" can list `auth_session` rows directly.
