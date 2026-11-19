# 0015. Secret sources and Fernet rotation

- Status: Proposed
- Date: 2026-11-19
- Deciders: Lattice (Holdfast). The owner sees it in the weekly digest.

## Context

PLAN 17.4 says Plaid and Anthropic credentials come from `.env` on a self-hosted deployment and from a per-household encrypted settings table when hosted. Hard rule 9 and ADR 0013 forbid a new external service and any paid KMS. Hard rule 3 and ADR 0007 keep bearer secrets encrypted at rest, and keep the runtime role `holdfast_app` a non-superuser with `NOBYPASSRLS`. Household tables have `FORCE ROW LEVEL SECURITY` (ADR 0008), which binds the table owner too.

F5 (roadmap; Bulwark roundtable 02, E1.2) asks for one resolver, Fernet key ids, and `make rotate-keys` before that code is written. F5a is already committed as `9adf0ca` on `feat/F5a-secret-token` (not on `origin/main`). `SecretToken` (`backend/app/security/secret_token.py`) masks `str`, `repr`, and `format`, and refuses JSON, pickle, and deepcopy. `reveal()` is the only plaintext path. This ADR does not replace that type.

Today `app.security.crypto` uses `MultiFernet`: `FERNET_KEY` is a comma-separated list, the first key encrypts, and every key decrypts. Stored ciphertext is a bare Fernet token (the version byte is `0x80`, so the ASCII form starts with `g`) with no key id. `crypto.rotate` re-wraps one token in memory; it does not walk rows and it would write another bare token.

The columns that hold that ciphertext are already in the schema. Names match the models, not PLAN §5's `plaid_item`:

| Column | Null | Primary key |
|---|---|---|
| `household_setting.value_enc` | not null | `(household_id, key)` |
| `bank_connection.access_token_enc` | not null | `id` |
| `app_user.totp_secret_enc` | nullable | `id` |

`app_user` has no RLS (login happens before a household is known). The other two tables do.

## Decision

### 1. One resolver, two sources, no fallback

`app.security.secrets.get_secret(household_id, name, *, settings, household_value_enc) -> SecretToken` is the only credential lookup. `APP_DEPLOYMENT` is `self_host` (the default when the variable is unset) or `hosted`. Any other value fails startup. The mode selects the only source. The other source is not a candidate, not an override, and not a fill-in for a missing value.

- `self_host`: read the process environment (`Settings`) for that name. Ignore `household_value_enc` even if the caller passed a row. Do not query `household_setting` for this lookup.
- `hosted`: decrypt `household_value_enc` from the row `(household_id, name)`. Ignore the environment value for that name. The service loads the row only in this mode, through a session already scoped to `household_id` (RLS). `get_secret` does not open a session. No row, or empty plaintext, is `SecretNotFound`.

A missing secret raises `SecretNotFound`. The message is a fixed string and includes neither source's value. Callers do not catch it and try the other source.

The allowlist is the whole set of names, in that module: `plaid_client_id`, `plaid_secret_sandbox`, `plaid_secret_production`, `simplefin_access_url`, `anthropic_api_key`, `openai_compat_api_key`. Any other name, including `fernet_key`, `session_secret`, database URLs, role passwords, `smtp_pass`, `ntfy_topic`, and `cf_api_token`, raises `SecretUnknown` and reads neither source. `PLAID_ENV` stays a deployment setting; the caller chooses which Plaid secret name to ask for. Linked-item tokens stay on `bank_connection.access_token_enc` and are not `get_secret` names.

Both modes return a `SecretToken`. The resolver does not call `reveal()`.

### 2. Rotation of exactly three columns

`make rotate-keys` runs a one-off command (not an API route, not an arq job). It re-encrypts exactly these columns: `household_setting.value_enc`, `bank_connection.access_token_enc`, and `app_user.totp_secret_enc`. It does not filter on `household_setting.key`. It does not touch `password_hash`, WebAuthn columns, session hashes, or recovery-code hashes. A later ADR adds any new ciphertext column; until then the rotator list is these three.

`FERNET_KEY` is unchanged in shape. The operator prepends the new key and keeps every key that still has rows. The command does not edit `.env` and does not drop old keys. It exits non-zero if any row fails. The operator removes old keys only after a clean run.

Key id: lowercase hex of the first 8 bytes of SHA-256 over the 32 raw bytes of that Fernet key (16 hex characters). The id is not secret. New ciphertext, including new application writes to these columns, is ASCII bytes:

`hf1.<kid>.<fernet-token>`

The Fernet token is the usual urlsafe-base64 token and contains no `.`. The primary key (the first `FERNET_KEY` entry) is the only key that seals.

Opening a value:

- SQL `NULL` is not ciphertext. The rotator skips that row: no decrypt, no encrypt, no `UPDATE`. Only `app_user.totp_secret_enc` can be null. Empty bytes are not null; they fail the row and are left unchanged. The command does not change `totp_enabled`.
- ASCII that starts with `hf1.`: parse exactly three dot-separated fields, with a 16-character lowercase kid. Decrypt with that key only. An unknown kid, a bad parse, or a token that fails that key fails the row. Do not try the other keys and do not treat it as legacy.
- ASCII that starts with `hf` and a digit, but is not a valid `hf1` value: unknown version. Fail the row. Do not trial-decrypt.
- Anything else that is valid ASCII, including today's bare Fernet tokens: legacy, recognized by having no key id. Trial-decrypt with the configured keys in list order (wrong keys fail the HMAC; they do not yield garbage). On success, seal under the primary kid. On failure, leave the bytes and fail the row.

A row whose kid is already the primary kid is not decrypted and not written. Legacy bytes are still rotated when the primary key already decrypts them, so a second format does not linger. `MultiFernet.rotate` is not the writer; it emits a bare token and would mint new legacy rows. The writer is seal of what open just returned.

Two rotations of one row cannot clobber each other. Each row is its own transaction. The command `SELECT ... FOR UPDATE` on the primary key, and the `UPDATE` is allowed only when the column is still equal to the bytes just read (`UPDATE ... SET col = :new WHERE pk AND col = :old`, plus `updated_at = now()` on `household_setting` and `bank_connection`). Zero rows means stop. Discard that plaintext. Re-read once under a new lock. If the fresh value is null, skip it. If it is already the primary kid, write nothing. If it still needs rotation, seal the fresh bytes only. A second failed predicate is a conflict: leave the row, exit non-zero, do not loop, and do not write plaintext taken from an earlier snapshot. Lock rows in primary-key order so two commands cannot deadlock. The loser of the race therefore either no-ops on the winner's envelope or rotates the ciphertext it actually locked, never the snapshot it lost.

The command logs counts only (rotated, already current, skipped null, failed, conflicted). It does not log ciphertext, plaintext, Fernet keys, or row contents.

Seeing every household does not grant `holdfast_app` `BYPASSRLS` or superuser. `app_user` is already visible without a household context; totp rotation uses that existing unscoped path. `household` and the two RLS tables are not. A follow-up migration adds `list_household_ids() RETURNS SETOF uuid`, body `SELECT id FROM public.household` only, `SECURITY DEFINER`, `search_path = pg_catalog, pg_temp`, `REVOKE ALL FROM PUBLIC`, `EXECUTE` granted to `holdfast_app`. `FORCE RLS` binds `holdfast_owner`, so the function owner is a new `NOLOGIN` role `holdfast_catalog` with `BYPASSRLS` and no password, created in `infra/postgres/init/00-roles.sh` the same way the other roles are. `holdfast_app` is not a member of it. `holdfast_owner` is a member only so a migration can assign function ownership; that membership does not set `BYPASSRLS` on `holdfast_owner` itself. The rotator then sets transaction-local `app.household_id` per id and updates those rows as `holdfast_app`. It connects with `DATABASE_URL`, never the admin URL, and never runs `SET row_security = off`.

### 3. SecretToken is the only in-process wrapper

F5a stands. Plaintext crosses out of `SecretToken` only through `reveal()`, and only at a credential boundary: the outbound call that presents a resolved credential, the TOTP check, and `seal` handing bytes to Fernet. Logs, `repr`, JSON, and pickle stay masked by the F5a type plus the existing redaction of `SecretToken` in `app.logging`. No second wrapper, subclass, or container type. `crypto.decrypt` stops being a public `str` API; callers use `open() -> SecretToken`.

### 4. Where the key lives

The Fernet key ring lives in the process environment (`FERNET_KEY`) for both `self_host` and `hosted`. Hosted is the same binary with `APP_DEPLOYMENT=hosted`. It is not a new service, account, or store. The key is not written to `household_setting`, `bank_connection`, `app_user`, or any other table those keys encrypt. `get_secret` cannot load it. Losing the ring still means those rows cannot be decrypted (PLAN §12, §16).

## Alternatives considered

- Household row overrides the environment, or the environment fills a missing row: a hosted household with no row would silently use the operator's Plaid or API key, and a self-hosted row would silently replace `.env`.
- Environment always wins: hosted households could not bring their own keys (PLAN 17.4).
- Paid KMS, Vault, or a cloud secret manager: a new external service (hard rule 9) and a paid plan (ADR 0013).
- A per-household data key stored beside the ciphertext: more machinery, and the master key would still have to live in the environment.
- A `kid` column: a migration for a fact the bytes already have room to carry, and legacy rows would still need a no-id rule.
- `BYPASSRLS` on `holdfast_app`, or a GUC the app can set to skip RLS: breaks ADR 0007 and ADR 0008 for every request, not just rotation.
- `MultiFernet.rotate` as the stored form: new rows would have no key id, so the legacy case never ends.

## Consequences

- Harbor's build (not this ADR) adds the envelope to `app.security.crypto`, `get_secret`, the rotator, `make rotate-keys`, `holdfast_catalog`, and `list_household_ids()`. The migration and the init-script role are protected-path reviews. Existing volumes need the role created by the admin init script before that migration.
- Tests that have to land with the build: self-host returns the env canary when a different canary is in `household_setting`; hosted returns the row and does not use the env canary; hosted with no row and self-host with no env raise `SecretNotFound` whose text contains neither canary; `fernet_key` reads neither source; a null `totp_secret_enc` is not updated; a bare Fernet token becomes `hf1.<primary kid>....`; a broken `hf1` value is not trial-decrypted; two concurrent rotations leave one ciphertext and the plaintext of the bytes that won the lock; a second run writes nothing; the runtime role is still not a superuser and still has no `BYPASSRLS`.
- Application writes to the three columns must seal as `hf1` in the same build, or rotation will keep finding fresh legacy rows.
- `SecretToken` remains the F5a type at `9adf0ca`. This ADR does not vendor another copy.
