# 0015. Secret sources and Fernet rotation

- Status: Proposed
- Date: 2026-11-19
- Deciders: Lattice (Holdfast). The owner sees it in the weekly digest.

## Context

PLAN 17.4 says Plaid and Anthropic credentials come from `.env` on a self-hosted deployment and from a per-household encrypted settings table when hosted. Hard rule 9 and ADR 0013 forbid a new external service and any paid KMS. Hard rule 3 and ADR 0007 keep bearer secrets encrypted at rest, and keep the runtime role `holdfast_app` a non-superuser with `NOBYPASSRLS`. Household tables have `FORCE ROW LEVEL SECURITY` (ADR 0008), which binds the table owner too.

F5 (roadmap; Bulwark roundtable 02, E1.2) asks for one resolver, Fernet key ids, and `make rotate-keys` before that code is written. F5a is merged on `main` as `9e16a5c`. `SecretToken` (`backend/app/security/secret_token.py`) masks `str`, `repr`, and `format`, and refuses JSON, pickle, and deepcopy. `reveal()` is the only plaintext path. This ADR does not replace that type.

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

`app.security.secrets.get_secret(household_id, name, *, settings, household_value_enc) -> SecretToken` is the only credential lookup. `APP_DEPLOYMENT` is `self_host` (the default when the variable is unset) or `hosted`. Any other value fails startup (`Settings` validation). The mode selects the only source. The other source is not a candidate, not an override, and not a fill-in for a missing value.

- `self_host`: read the process environment (`Settings`) for that name. An empty or whitespace-only value is `SecretNotFound`, the same as unset. Ignore `household_value_enc` even if the caller passed a row. Do not query `household_setting` for this lookup.
- `hosted`: decrypt `household_value_enc` from the row `(household_id, name)`. Ignore the environment value for that name. The service loads the row only in this mode, through a session already scoped to `household_id` (RLS). `get_secret` does not open a session. No row, or empty plaintext, is `SecretNotFound`.

A missing secret raises `SecretNotFound`. The message is a fixed string and includes neither source's value. Callers do not catch it and try the other source.

The allowlist is the whole set of names, in that module: `plaid_client_id`, `plaid_secret_sandbox`, `plaid_secret_production`, `simplefin_access_url`, `anthropic_api_key`, `openai_compat_api_key`. Any other name, including `fernet_key`, `session_secret`, database URLs, role passwords, `smtp_pass`, `ntfy_topic`, and `cf_api_token`, raises `SecretUnknown` and reads neither source. `PLAID_ENV` stays a deployment setting; the caller chooses which Plaid secret name to ask for. Linked-item tokens stay on `bank_connection.access_token_enc` and are not `get_secret` names.

Both modes return a `SecretToken`. The resolver does not call `reveal()`.

### 2. Rotation of exactly three columns

`make rotate-keys` runs a one-off command (not an API route, not an arq job). It re-encrypts exactly these columns: `household_setting.value_enc`, `bank_connection.access_token_enc`, and `app_user.totp_secret_enc`. It does not filter on `household_setting.key`. It does not touch `password_hash`, WebAuthn columns, session hashes, or recovery-code hashes. A later ADR adds any new ciphertext column; until then the rotator list is these three.

`FERNET_KEY` is unchanged in shape. The command does not edit `.env` and does not drop old keys. Duplicate keys, or two keys with the same kid, in the ring fail startup of the api, the worker and the command (`DuplicateKeyError`).

Operator key-change order. Do not skip or reorder:

1. Prepend the new key to `FERNET_KEY` and keep every old key.
2. Restart `api` and `worker` so every writer seals under the new key. A writer still on the old ring would keep minting old-kid rows.
3. Run `make rotate-keys` (`./scripts/dev.ps1 rotate-keys`).
4. Run it again. Proceed only when it reports `rotated=0 failed=0 conflicted=0 residual=0`.
5. Only then remove the retired key from `FERNET_KEY` and restart.

The command refuses to report clean while any row, after its pass, is not an `hf1` envelope under the primary kid. After the rotation pass it re-reads every row of the three columns without decrypting, and counts as `residual` anything non-null that is not on the primary kid (failed rows, conflicts, rows an old-key writer added mid-run). It exits 0 only when failed, conflicted and residual are all zero; otherwise it exits 1 with a "keep every key" message. A bad or duplicate ring exits 2.

Retired keys decrypt every backup taken before the rotation. Keep each retired key offline, outside the live `.env`, for as long as any backup that depends on it is retained, and destroy it with the last such backup.

Each run writes one `audit_log` row (`action = 'keys.rotate'`, no household, `outcome` success or failure) whose `detail` holds the counts only.

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

Seeing every household does not grant `holdfast_app` `BYPASSRLS` or superuser. `app_user` is visible without a household context, and every household has at least one `app_user` row (first-run setup creates the household and its owner in one unit). The rotator therefore takes the household list from `SELECT DISTINCT household_id FROM app_user` on the existing unscoped path, sets transaction-local `app.household_id` per id with `household_wide=True` (a background job must see every person's rows; `security/rotation.py` is the only `app/security/` file the household-wide guard test allows), and updates `household_setting` and `bank_connection` as `holdfast_app`. It connects with `DATABASE_URL`, never the admin URL, and never runs `SET row_security = off`.

Amendment (F5c): the earlier draft of this section proposed a `list_household_ids()` SECURITY DEFINER function owned by a new `NOLOGIN` `BYPASSRLS` role `holdfast_catalog`. F5c builds without it: it needs no migration and no new privileged role, and the residual scan above covers the one gap (a household with ciphertext rows but no user, which setup cannot create) only for households that `app_user` lists. If a later story can create a user-less household, it must add that function then. This keeps ADR 0007 and 0008 untouched.

### 3. SecretToken is the only in-process wrapper

F5a stands. Plaintext crosses out of `SecretToken` only through `reveal()`, and only at a credential boundary: the outbound call that presents a resolved credential, the TOTP check, and `seal` handing bytes to Fernet. Logs, `repr`, JSON, and pickle stay masked by the F5a type plus the existing redaction of `SecretToken` in `app.logging`. No second wrapper, subclass, or container type: `SecretToken` is `@typing.final` and `__init_subclass__` raises `TypeError`. `crypto.decrypt` stops being a public `str` API; callers use `open() -> SecretToken`.

### 4. Where the key lives

The Fernet key ring lives in the process environment (`FERNET_KEY`) for both `self_host` and `hosted`. Hosted is the same binary with `APP_DEPLOYMENT=hosted`. It is not a new service, account, or store. The key is not written to `household_setting`, `bank_connection`, `app_user`, or any other table those keys encrypt. `get_secret` cannot load it. Losing the ring still means those rows cannot be decrypted (PLAN §12, §16).

### 5. Outbound URLs a household supplies (SSRF)

In `hosted` mode a household can enter URLs the server then fetches (`simplefin_access_url`, an OpenAI-compatible base URL). Every connector that fetches such a URL must first call `app.security.outbound.resolve_outbound(url)` and send the request to the pinned address it returns. The validator: `https` only; resolve the host and require every answer to be globally routable (loopback, private, link-local, multicast, reserved, unspecified, carrier-grade NAT, IPv4-mapped, NAT64, 6to4 and Teredo forms are refused, including as IP literals); pin the validated IP for the request (Host header and SNI keep the original name); callers set `follow_redirects=False` and re-validate any redirect target themselves. Errors carry a reason code and never echo the URL, which may hold credentials. Connectors do not exist yet; a connector PR that fetches a household URL without this call fails review.

### 6. No plaintext-bound context (AAD substitute): decided no, for now

Fernet has no associated data. Binding `household_id|column|name` inside the plaintext would stop a database writer from moving one household's ciphertext into another row. It is not implemented now. A writer who can edit these columns can already destroy them, the three columns have no household-supplied writers yet, and the plaintext framing would need the row identity on every `open` and `seal` call site, which do not exist for `household_setting` or `bank_connection`. The connector story that adds those writers must revisit it. The path is a new envelope version, `hf2`, carrying the bound context; legacy and `hf1` rows keep opening because parsing is by version prefix, and the rotator already fails unknown versions until it learns `hf2`.

## Alternatives considered

- Household row overrides the environment, or the environment fills a missing row: a hosted household with no row would silently use the operator's Plaid or API key, and a self-hosted row would silently replace `.env`.
- Environment always wins: hosted households could not bring their own keys (PLAN 17.4).
- Paid KMS, Vault, or a cloud secret manager: a new external service (hard rule 9) and a paid plan (ADR 0013).
- A per-household data key stored beside the ciphertext: more machinery, and the master key would still have to live in the environment.
- A `kid` column: a migration for a fact the bytes already have room to carry, and legacy rows would still need a no-id rule.
- `BYPASSRLS` on `holdfast_app`, or a GUC the app can set to skip RLS: breaks ADR 0007 and ADR 0008 for every request, not just rotation.
- `MultiFernet.rotate` as the stored form: new rows would have no key id, so the legacy case never ends.

## Consequences

- Harbor's build (F5c) adds the envelope to `app.security.crypto`, `get_secret`, the rotator, `make rotate-keys` and the `rotate-keys` compose service (`tools` profile, `holdfast_app` role, backend network only, no ports), and the outbound validator. There is no migration and no new role.
- Tests that have to land with the build: self-host returns the env canary when a different canary is in `household_setting`; hosted returns the row and does not use the env canary; hosted with no row and self-host with no env raise `SecretNotFound` whose text contains neither canary; `fernet_key` reads neither source; a null `totp_secret_enc` is not updated; a bare Fernet token becomes `hf1.<primary kid>....`; a broken `hf1` value is not trial-decrypted; two concurrent rotations leave one ciphertext and the plaintext of the bytes that won the lock; a second run writes nothing; a duplicate ring fails; an `hf1` value relabelled with another kid fails; the runtime role is still not a superuser and still has no `BYPASSRLS`.
- Application writes to the three columns must seal as `hf1` in the same build, or rotation will keep finding fresh legacy rows.
- `SecretToken` remains the F5a type (merged `9e16a5c`); F5c only makes it final. This ADR does not vendor another copy.
