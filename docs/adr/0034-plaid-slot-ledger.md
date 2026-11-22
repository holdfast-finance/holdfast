# 0034. Plaid slots: a per-client ledger, reserved at Link-token time, cross-checked against the dashboard

- Status: Accepted (revision 2, after Gadfly's B8 and S4)
- Date: 2026-11-21
- Deciders: Lattice (architect). Keel ruled on B8. Bulwark reviews P4a and P4.

## Context
Hard rule 4 requires the slot guard: a typed confirmation at 8 or more, and never an 11th. Plaid's
Trial allows 10 production Items **per client id**, ever. `/item/remove` returns nothing.

The Item exists at Plaid as soon as the person finishes Link, before Holdfast sees a
`public_token`. Every fact Holdfast keeps about slots and tokens lives in Postgres, and the access
tokens are sealed under `FERNET_KEY`. Both can be lost (Gadfly B8). Plaid offers no API that counts
a client's Items (ADR 0023).

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
| `link_token_fp` | |
| `dashboard_count` | smallint, 0 to 10 |
| `item_id` | unique, nullable |
| `bank_connection_id` | `SET NULL` |
| `created_by_user_id` | |
| `created_at`, `resolved_at` | |

Access and constraints:
- Forced RLS with the standard policy, for reads by people.
- `holdfast_app` gets SELECT, INSERT and UPDATE only, with no DELETE grant. A trigger refuses
  DELETE outside a household cascade.
- Allowed transitions: `reserved` → any other state, and `unknown` → `abandoned`.
- **Keyed by client, not household:**
  - The partial unique index allowing one `reserved` row is on `(client_fp, env)`.
  - The count comes from `plaid_slot_count(client_fp, env) RETURNS integer`, a `SECURITY DEFINER`
    function that returns a number and nothing else. It follows the pattern #31 uses for
    `list_household_ids()`: an owner-only SELECT policy, `search_path` pinned, EXECUTE only for
    `holdfast_app`.
  - Two households on one client therefore share one count.

### 2. Every production new-Item link is confirmed against the dashboard
`POST /api/connections/plaid/link-plan` is a dry call: no Plaid call and no token. It returns:
- the ledger count
- the effective `products`, `optional_products` and `days_requested` (Phase 2 contract D5, D6, B4)
- the list of existing connections, for the duplicate-login check

`POST /api/connections/plaid/link-token` for a new production Item requires:
- `dashboard_count`: the production Item count the owner reads in Plaid's dashboard. Holdfast uses
  `n = max(ledger count, dashboard_count)`.
- `not_a_duplicate: true`, after the owner has seen the existing connections.
- the exact phrase `use slot <n + 1> of 10`. When the effective products or days differ from the
  contract defaults, the phrase is `use slot <n + 1> of 10 with <products> for <days> days`.

Outcomes:
- `n >= 10`: `409 no_slots_left`, before any Plaid call.
- A missing or wrong field: `409 confirm_required`.
- Update-mode (relink) tokens reserve nothing and need step-up only.
- Sandbox tokens write `env = 'sandbox'` rows. They are never counted, need no dashboard count, and
  are refused on an instance holding real data (contract B5).

This is stricter than hard rule 4, which asks for a phrase only from 8. The dashboard count is what
makes the guard survive a restore.

### 3. Resolving a reservation
- **Exchange is idempotent on `reservation_id`.** The browser may retry within the `public_token`
  lifetime (30 minutes). The server:
  1. exchanges the token
  2. seals the access token, inserts `bank_connection` (status `pending_setup`, `client_fp`) and
     sets the slot to `linked` with the `item_id`, and **commits all of that first**
  3. only then calls `/accounts/get`
  A crash after the commit loses nothing.
- A `reserved` row is resolved through `/link/token/get` on its Link token. Its `link_sessions`
  results report the Items added, and **VERIFY** this on Trial in P4. That call:
  - recovers a lost `public_token` (exchange it)
  - confirms "no Item" before `abandoned`
  - lets a stuck row (crashed tab) be resolved at once instead of after 4 hours
- `onExit` from the browser only *triggers* that check. It never marks `abandoned` by itself.
- When `/link/token/get` is unavailable on the plan, a reservation older than 4 hours becomes
  `unknown` and still counts. The owner may move `unknown` to `abandoned` with step-up and the
  phrase `I did not finish this link`.
- Plaid's own limit is the backstop. The Trial limit error name (`TRIAL_CONNECTION_LIMIT` in
  Plaid's docs) and where it surfaces (Link `onExit`, not the exchange) are **VERIFY** items for P4.
  The mapping in either place is `no_slots_left`.

### 4. What loses slots, plainly
| Event | Access tokens | Slot ledger | Result |
|---|---|---|---|
| Restore a backup **older** than a link | that Item's token is gone; Plaid never reissues it | under-counts by the links after the backup | those Items are orphaned and their slots are spent for good; relinking them costs new slots. The dashboard count in §2 keeps the next guard correct |
| Restore a backup taken **after** the last link (DB and the same `FERNET_KEY`) | intact | intact | nothing lost |
| Fresh install, same Plaid client | none | empty | every old Item is orphaned unless the old DB and key are restored. The dashboard count keeps the guard correct |
| New or lost `FERNET_KEY` (`gen-keys.py --force`, a lost `.env`) | every `hf1` value is unreadable: Plaid tokens, SimpleFIN access URLs, in-app keys, TOTP secrets | intact but useless | every Plaid Item is orphaned (slots spent), SimpleFIN needs a fresh setup token (free), keys must be re-entered and TOTP re-enrolled. `make rotate-keys` with the old key **prepended**, never replaced, is the only safe key change (ADR 0015) |
| Delete a connection in the app | removed | unchanged | the slot stays spent, as Plaid's own count does |

The setup check reports `tokens_unreadable` when any `bank_connection` fails to open, and
`ledger_orphans` when a `linked` slot row has no connection.

### 5. Backups the owner must keep (an M-live precondition)
- A `pg_dump` of the instance database and the document store, kept off the machine. Take one
  before and right after every production link.
- **Every** key in `FERNET_KEY`, kept in a password manager, **separately** from the dump.
- The Plaid client id and secrets (re-enterable from the dashboard), and the SimpleFIN Bridge login.
- One restore drill into the throwaway sandbox project before M-live, which proves the dump opens
  and the key decrypts.

`scripts/backup.sh` is not implemented yet (Phase 9). The contract adds a minimal Harbor slice (H1)
before M-live.

## Alternatives considered
- **Count `bank_connection` rows:** deletion lowers it, and failed exchanges never add one.
- **A typed slot floor stored once:** it goes stale after the next restore. A count typed per link
  doesn't.
- **A per-household count:** two households on one client could each count to 10 (B8).
- **Trust Link `onExit`:** the client's word would decide what is counted (S4).

## Consequences
- P4 owns the guard, the plan call, the idempotent exchange and `/link/token/get` resolution.
- Tests:
  - the guard at ledger counts of 7, 8, 9 and 10, and with a dashboard count above the ledger
  - deleting a connection doesn't lower the count
  - two concurrent reservations from two households on one client leave one `reserved` row
  - a sandbox reservation is not counted
  - a relink reserves nothing
  - an 11th request is refused before any Plaid call
  - an exchange retried after a crash between commit and `/accounts/get` creates no second
    connection
  - `onExit` alone never yields `abandoned`
