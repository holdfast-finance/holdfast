# 0034. Plaid slots: a per-client ledger, reserved at Link-token time, cross-checked against the owner's count

- Status: Accepted (revision 3, after Gadfly round 2 (R2-B5, R2-S1, R2-S4) and Bulwark's
  must-fixes 1, 2 and 5 on PR #62)
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel ruled on B8 and deleted the override phrase. Bulwark reviews
  P4a and P4-link.

## Context
Hard rule 4 requires the slot guard: a typed confirmation at 8 or more, and never an 11th. Plaid's
Trial allows 10 production Items per client id, ever. `/item/remove` returns nothing. The Item
exists at Plaid when the person finishes Link, before Holdfast sees a `public_token`. Every fact
Holdfast keeps lives in Postgres, under `FERNET_KEY`.

## Decision

### 1. A ledger keyed by Plaid client
P4a adds `plaid_link_slot`:

| Column | Notes |
|---|---|
| `id` | |
| `household_id` | the household that asked, for RLS |
| `client_fp` | 16 lowercase hex characters of SHA-256 of the client id |
| `env` | |
| `state` | `reserved`, `linked`, `abandoned` or `unknown` |
| `link_token_enc` | bytea NULL |
| `owner_count` | smallint, 0 to 10 |
| `item_id` | unique, nullable |
| `bank_connection_id` | `SET NULL` |
| `created_by_user_id` | |
| `created_at`, `resolved_at` | |
| `flag` | text NULL, for example `second_public_token` |

Access and constraints:
- Forced RLS with the standard policy.
- `holdfast_app` gets SELECT, INSERT and UPDATE only, with no DELETE grant. A trigger refuses
  DELETE outside a household cascade.
- Allowed transitions: `reserved` → any other state, and `unknown` → `abandoned`.
- A partial unique index allows one `reserved` row per `(client_fp, env)`.
- `plaid_slot_count(client_fp, env) RETURNS integer` is `SECURITY DEFINER` and returns a number
  only. It follows #31's `list_household_ids` pattern: an owner-only SELECT policy, `search_path`
  pinned, EXECUTE only for `holdfast_app`.

**What counts.** The count is every row for `(client_fp, env = 'production')` in state `reserved`,
`linked` or `unknown`. Only `abandoned` is excluded. `env` is written from the server's
`PLAID_ENV`, never from the request.

**As built in P4a (migration `0011_connector_link`).**
- *How the DELETE trigger spots a household cascade.* `plaid_link_slot_no_delete` is
  `SECURITY DEFINER`, so the owner-only household policy from 0010 lets it see every household.
  It refuses the DELETE while the row's household still exists. Inside the household's own
  cascade that row is already gone, so the cascade goes through. This binds the owner role too.
  `pg_trigger_depth()` was rejected because any nested trigger would pass it.
- `plaid_link_slot_guard` enforces the transitions. It also keeps `client_fp`, `env`,
  `owner_count`, `created_by_user_id`, `household_id` and `created_at` fixed, and a recorded
  `item_id` never changes.
- CHECKs: `state = 'reserved' OR link_token_enc IS NULL` (Bulwark), `(state = 'reserved') =
  (resolved_at IS NULL)`, a `linked` row has an `item_id` and an `abandoned` row has none, a
  production row has an `owner_count` (0 to 10), and `flag` is a short code, never free text.
- The downgrade of 0011 refuses to run while any production row is not `abandoned` (Bulwark M2
  on #74). Dropping the ledger and upgrading again would otherwise reset the count and let an
  11th Item through.
- `created_by_user_id` has no FK, like `import_run.reviewed_by_user_id`, so the ledger outlives
  the user who linked.
- `plaid_slot_count(client_fp, env)` counts the given `env`. Callers pass `'production'` for the
  guard, so sandbox rows never count toward it.

### 2. Every production new-Item link is confirmed
`POST /api/connections/plaid/link-plan` is a dry call with no Plaid call. It returns:
- the ledger count
- the products and `days_requested` for the requested `login_kind` (contract §5.1; an allowlist,
  so there is no override path)
- the existing connections, for the duplicate-login check

`POST /api/connections/plaid/link-token` for a new production Item requires:
- `owner_count`: the owner's count of production Items on this Plaid client. Holdfast uses
  `n = max(ledger count, owner_count)`. What the owner reads it from is **VERIFY** in P4-link
  (R2-S4):
  - if Plaid's dashboard shows the Trial's lifetime usage, that number
  - if it shows only active Items, which under-count after removals and restores, the owner's
    written slot log (PLAN 3.2)
  The link-plan copy names the source the check found.
- `not_a_duplicate: true`, after the existing connections were shown.
- The exact phrase `use slot <n + 1> of 10`. There is no other phrase variant.

Outcomes:
- `n >= 10`: `409 no_slots_left`, before any Plaid call.
- A missing or wrong field: `409 confirm_required`.
- Update-mode (relink) tokens reserve nothing and need step-up only.
- Sandbox tokens write `env = 'sandbox'` rows, which are never counted, and follow ADR 0033 §7.

### 3. Resolving a reservation, and the Link token (R2-B5)
**The Link token is kept, sealed, only while the reservation is open.**
- It is sealed `hf2`, bound to `household_id|plaid_link_slot|link_token_enc|id`, into
  `link_token_enc`.
- It is set to NULL in the same UPDATE that leaves `reserved`, and after 4 hours (the Link token
  lifetime) at the latest, when the row becomes `unknown`.
- It is the third ciphertext column under ADR 0015 §2 and F5d. The rotator re-seals it, and a NULL
  value is skipped.
- Canary tests cover it in logs, responses, audit rows and job payloads.
- Bulwark rules on it as a short-lived credential in P4-link's review.

**Resolution uses `/link/token/get`** on the unsealed token. Its `link_sessions` results report the
Items added (**VERIFY** on Trial). It:
- recovers a lost `public_token` (exchange it)
- confirms "no Item" before `abandoned`
- resolves a crashed tab's reservation at once

Browser `onExit` only triggers this check.

**Fallback.** If `/link/token/get` is unavailable on the plan:
- After 4 hours the row becomes `unknown`, still counts, and its token is NULLed.
- The owner may move `unknown` to `abandoned` with step-up and the phrase
  `I did not finish this link`.
- The per-client `reserved` lock blocks new links for at most those 4 hours. The owner can end it
  sooner with the same abandon step, but only after Plaid has been asked: while the reservation is
  under 4 hours old and holds a sealed token, abandon refuses if Plaid can't answer, if
  `PLAID_ENV` differs from the reservation's env ("switch back to <env> to resolve"), or if the
  token can't be opened (a lost `FERNET_KEY`). Each could hide an Item the ledger hasn't seen.
  After 4 hours the token has expired and the phrase alone suffices.

**Exchange.**
- It is idempotent on `reservation_id`. The `public_token` must be bound to its reservation: same
  household, `created_by_user_id` is the caller, state `reserved`.
- A **different** `public_token` arriving on a reservation already `linked` is a new Item. It gets
  its own counted row with `flag = 'second_public_token'`, and the connections list shows it.
- An update-mode exchange never creates a connection.
- Order of work:
  1. parse the access token straight into `SecretToken` and seal it `hf2`, bound to its
     `bank_connection` row. The row id is generated before sealing.
  2. insert the connection (status `pending_setup`, `client_fp`), and set the slot to `linked` with
     its `item_id`
  3. **commit**
  4. only then call `/accounts/get`
- No Plaid response body is ever logged.

Plaid's own limit is the backstop. The Trial limit error name and where it surfaces (Link `onExit`
or the exchange) are **VERIFY**. Both map to `no_slots_left`.

### 4. What loses slots, plainly
| Event | Access tokens | Slot ledger | Result |
|---|---|---|---|
| Restore a backup older than a link | that Item's token is gone; Plaid never reissues it | under-counts | the Item is orphaned and its slot spent for good. The owner's count in §2 keeps the next guard right |
| Restore a backup taken after the last link (DB and the same `FERNET_KEY`) | intact | intact | nothing lost |
| Fresh install, same client | none | empty | old Items orphaned unless the old DB and key are restored. The owner's count keeps the guard right |
| New or lost `FERNET_KEY` | every sealed value unreadable (tokens, access URLs, in-app keys, TOTP) | intact, useless | every Plaid Item orphaned, SimpleFIN re-claimed (free), keys re-entered, TOTP re-enrolled. Only `make rotate-keys` with the old key **prepended** is safe |
| Delete a connection | removed | unchanged | the slot stays spent |

The setup check reports `tokens_unreadable` (it opens each token without `reveal()`) and
`ledger_orphans` (a `linked` row with no connection).

### 5. Backups the owner keeps (an M-link precondition)
- A `pg_dump` of the instance database plus the document store, kept off the machine, before and
  right after every production link.
- Every key in `FERNET_KEY`, in a password manager, **separately** from the dumps.
- The Plaid keys (re-enterable from the dashboard) and the slot log.

The H1 runbook (`docs/ops/backup.md`) gives the commands. Its one verified restore uses a
**synthetic** dump with a synthetic key, in the throwaway sandbox project. A real dump and the real
`FERNET_KEY` never enter the sandbox project or any project agents operate. If the owner wants to
prove their real backup restores, the runbook describes an owner-only scratch project, destroyed
afterwards, that agents never touch.

## Alternatives considered
- **Count `bank_connection` rows:** deletion lowers the count.
- **Store only a fingerprint of the Link token (revision 2):** `/link/token/get` needs the token
  itself (R2-B5).
- **A typed floor stored once:** stale after the next restore.
- **A per-household count:** two households on one client could each reach 10.
- **Trust `onExit`:** the client's word would decide what is counted.
- **The env-override phrase (revision 2):** deleted. Products are an allowlist (Bulwark 2).

## Consequences
- Tests:
  - the guard at 7, 8, 9 and 10, and with an owner count above the ledger
  - deleting a connection leaves the count unchanged
  - two households on one client get one `reserved` row
  - sandbox rows are not counted
  - a relink reserves nothing
  - an 11th request is refused before any Plaid call
  - an exchange retried after a crash between commit and `/accounts/get` makes no second
    connection
  - a second `public_token` on a `linked` row is counted and flagged
  - `onExit` alone never abandons
  - `link_token_enc` is NULL after every resolution and after 4 hours
  - the rotator re-seals a live `link_token_enc`
