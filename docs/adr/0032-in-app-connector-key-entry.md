# 0032. Connector keys can be entered in the app; exactly one source per key group

- Status: Proposed (revision 2, after Gadfly's S5). Bulwark threat-models it before K1 merges. It
  amends ADR 0015 §1 and settles ADR 0023 §7. K1 is scheduled **after** M-live (Keel, 2026-11-21):
  the owner's first link uses the `.env` keys of the dedicated instance.
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel. The owner asked for in-app entry (ADR 0023 §7).

## Context
ADR 0015 gives each deployment mode one source. `self_host` reads the environment and ignores
`household_setting`, and `hosted` reads only `household_setting`. Its reason is that a stored row
must never silently replace an operator's key, and the operator's key must never silently fill in
for a household. ADR 0023 §7 records that the owner does not accept "edit `.env` and restart" as
the way a new self-hoster brings their Plaid keys, and leaves the design open. Number 0031 is taken
by another branch, so this ADR is 0032.

Three more facts matter:
- The dev `.env` on the owner's machine holds production Plaid keys today. The owner queue's first
  item is to move them out, and agents stay off live Plaid until that happens (SDLC §3).
- `household_setting(household_id, key, value_enc)` already exists, has RLS, and is one of the
  three columns the rotator re-seals (ADR 0015 §2). No migration is needed to store keys there.
- Plaid's Item limit and its Items belong to a client id. Changing the client id orphans every
  linked Item's access token.

## Options
| | Option | For | Against |
|---|---|---|---|
| A | Keep `.env` only, plus a `make plaid-keys` prompt that writes `.env` | No ADR 0015 change | Still a terminal and a restart. Keys sit in the file agents must not read. The owner rejected it |
| B | In-app entry in `self_host`, and the row **overrides** the environment | Simple | The silent replacement ADR 0015 forbids |
| C | In-app entry, with the environment as a **fallback** when no row exists | Simple | The silent fill-in ADR 0015 forbids |
| D | **In-app entry, with exactly one source per key group.** Env only: env. Row only: row. Both: conflict, not used | No precedence to get wrong. Visible state. No restart | One new error type and a settings surface |
| E | The API writes `.env` or a secrets file on a volume | Keeps "env is the source" | Gives the API container write access to its own configuration. A new store with no rotation |

## Decision
**Option D.**

1. **Key groups.** The resolver decides the source per **group**, not per name. Plaid's
   `plaid_client_id`, `plaid_secret_sandbox` and `plaid_secret_production` are one group: all of
   them come from env, or all from rows. `openai_compat_base_url` and `openai_compat_api_key` are
   another group: changing the host needs step-up, is audited, and requires the key to be entered
   again. Any other name is a group of one. In `self_host`,
   `get_secret(household_id, name, *, settings, household_value_enc)` looks at every member of the
   name's group in both sources (the caller passes the group's rows):
   - some member set in env (non-blank), and no member has a row: the env value.
   - some member has a row, and no member is set in env: the row.
   - members in both sources, even different members: `SecretConflict`. Nothing in the group is
     used, and the message is a fixed string naming neither value.
   - neither source has the name: `SecretNotFound`.
   `hosted` is unchanged: row only, env ignored. The caller loads the row through the household's
   RLS session in both modes, and `get_secret` still opens no session.
2. **Entry API** (owner role, step-up re-auth within 5 minutes, CSRF header, audited as
   `settings.secret_set` / `settings.secret_clear` with the name only):
   - `GET /api/settings/secrets` returns, for each allowlisted name, one of `not_set`,
     `set_in_app`, `set_in_env` or `conflict`. It never returns the value, a prefix, a suffix or a
     length.
   - `PUT /api/settings/secrets/{name}` with `{value}` seals the value as `hf2`, bound to
     `(household_id, household_setting, value_enc, name)`, into `household_setting` (F5d). It returns `409 managed_in_env` when the env value is set. Values are
     trimmed. Empty, line-broken or over-length values are refused, and the P3 client's
     malformed-key check runs here too.
   - `DELETE /api/settings/secrets/{name}` removes the row.
3. **Changing `plaid_client_id` while live Items exist**: through `PUT`, it needs a typed
   confirmation (`replace plaid keys`). Editing `.env` bypasses that prompt, so the check that
   matters runs **at use time**: the sync job and the Link service compare the resolved client's
   fingerprint with each connection's `client_fp`. P4 and P5 run this check, so it exists before
   K1. Items belong to the client that created them, so update mode can't move them. Every connection whose `bank_connection.client_fp` differs from the new client's
   fingerprint gets status `error` with code `client_changed`. Sync Health says those Items belong
   to the previous Plaid account, and putting the old keys back restores them. Client ids are
   compared as fingerprints (the first 16 hex characters of a SHA-256) and are never logged.
4. **`PLAID_ENV` stays a deployment setting** in this ADR. Choosing Sandbox or Production in the
   app is a separate decision, because it moves hard rule 4's default from a file to a row. Until
   then the owner sets it in `.env` once.
5. **A conflict is one household-level state, not N connection errors.** A sync or Link call
   that meets `SecretConflict` fails its `sync_run` with `error_class = 'SecretConflict'` and leaves
   every connection's `status` unchanged. Nothing new is stored. The Connections page reads
   `GET /settings/secrets`, which reports `conflict` for the group, and shows one banner naming the
   group and both sources. Sync Health shows the connections as "waiting for keys", not as broken.
6. **Env changes need a container recreate.** Compose reads `.env` when containers are created.
   Deleting env keys takes effect only after `docker compose up -d --force-recreate api worker`.
   Until then `PUT` answers `409 managed_in_env`. The copy and `docs/setup/plaid.md` say so.
7. Rate limit: 10 writes per hour per household. Credentials are resolved per call, so nothing
   cached needs invalidating and no restart is needed for in-app changes.

## Alternatives considered
See the options table. Option A was rejected by the owner. Options B and C are the exact failure
modes ADR 0015 names. Option E widens what the API process can write.

## Consequences
- K1 (Phase 2 contract) builds this after F5d and M-live.
  - `PUT` and `DELETE` return a status only.
  - A `plaid_client_id` change audits the new `client_fp`.
  - The runbook documents step-up when TOTP can't be decrypted after a lost `FERNET_KEY`: passkey
    only, or recovery codes. It edits `app/security/secrets.py` (protected path) and adds
  `app/services/secret_settings.py`, `app/api/routes_settings_secrets.py` and tests. When K1 merges,
  the K1 PR adds "Amended by 0032" to ADR 0015 §1 and to ADR 0023 §7.
- ADR 0015's tests change. A mixed group (client id in env, secret in a row) raises
  `SecretConflict`. A changed client in env marks its connections `client_changed` at the next
  sync. "Self-host returns the env canary when a different canary is in
  `household_setting`" becomes "self-host with both raises `SecretConflict` and leaks neither
  canary". New tests: row-only self-host returns the row. `PUT` with env set is `409`. `GET` never
  contains either canary. A conflict makes the Plaid setup check report `keys_present: conflict`.
- For the owner, this turns owner-queue item 1 into a concrete step: delete the production keys
  from the dev `.env`, then enter them on their own instance's Connections page. After that, no
  plaintext key sits in a file on the machine.
- `simplefin_access_url` leaves the allowlist (ADR 0030). The remaining names are the Plaid trio,
  `anthropic_api_key` and `openai_compat_api_key`, and all of them gain in-app entry.
