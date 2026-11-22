# 0034. Plaid slots are reserved when a Link token is issued and never released by deletion

- Status: Accepted
- Date: 2026-11-21
- Deciders: Lattice (architect). Bulwark reviews P4a and P4.

## Context
Hard rule 4: no production Item without the slot guard. The rules are a typed confirmation at 8 or
more, and never an 11th. Plaid's Trial allows 10 production Items per client, ever, and
`/item/remove` doesn't return a slot (ADR 0023). ADR 0023's consequences say the setup check's
count, which runs `count(bank_connection)` per household, is display-only. The real guard must
count from a tombstone keyed by Plaid client, and deleting a connection must not lower it.

The Item is created at Plaid when the person finishes Link. That happens **before** Holdfast sees
a `public_token`. An exchange that fails, a closed tab or a lost `onSuccess` can therefore spend a
slot that no `bank_connection` row records.

## Decision
1. **New table `plaid_link_slot`** (P4a migration). Columns:
   - `id`, `household_id`
   - `client_fp`: the first 16 hex characters of SHA-256 of the client id
   - `env`: `sandbox` or `production`
   - `state`: `reserved`, `linked`, `abandoned` or `unknown`
   - `item_id`: nullable, unique
   - `bank_connection_id`: nullable, `SET NULL`
   - `created_by_user_id`, `created_at`, `resolved_at`

   It has `household_id`, forced RLS and the standard policy. `holdfast_app` gets SELECT, INSERT
   and UPDATE only. There is **no DELETE grant**, and a trigger refuses DELETE outside a household
   cascade. Allowed transitions: `reserved` to any other state, and `unknown` to `abandoned`.
   `linked` and `abandoned` are final.
2. **Reservations:**
   - A production Link token for a **new** Item first inserts a `reserved` row. A partial unique
     index allows one `reserved` row per `(household_id, client_fp, env)`, so two tabs can't race
     to slot 11.
   - Update-mode (relink) tokens reserve nothing.
   - Sandbox tokens insert rows with `env = 'sandbox'`. They are never counted, but they exercise
     the same path.
3. **Count** = rows with `state IN ('reserved', 'linked', 'unknown')` for the household's current
   `client_fp` in `production`. Guard:
   - count ≥ 10: refuse, `409 no_slots_left`.
   - count ≥ 8 (PLAN 7.1): the request must carry the exact phrase `use slot <count + 1> of 10`.
     Otherwise `409 confirm_required`.
   - count < 8: no phrase is needed. The warning text still shows the count.
   - The phrase is computed by the server and compared exactly.
4. **Resolution:**
   - A successful exchange sets `linked` and `item_id`, and links the connection.
   - Link `onExit` with no public token sets `abandoned`.
   - A `reserved` row older than the Link token's lifetime (4 hours) becomes `unknown` the next
     time the guard reads it, and still counts.
   - The owner may move `unknown` to `abandoned` only with step-up and the phrase
     `I did not finish this link`. The move is audited.
5. **Plaid's own limit is the backstop, not the guard.** Plaid's `TRIAL_CONNECTION_LIMIT` on an 11th
   Item is mapped to `no_slots_left` and marks the reservation `abandoned`, because no Item was
   created. The local guard exists for paid plans and for the warning at 8.
6. The setup check (ADR 0023) switches its `item_slots` step to this count in P4.

## Alternatives considered
- **Count `bank_connection` rows:** deleting a connection lowers the count, and a failed exchange
  never adds one (ADR 0023).
- **Reserve at exchange time:** too late. The Item already exists.
- **Count unknown reservations as free:** the counter would drift below Plaid's real count, and the
  warning at 8 would come late.
- **A global (non-RLS) table:** in `self_host` each household brings its own client. A hosted
  operator with shared keys needs a cross-household count, which is a separate decision taken when
  hosted Plaid is scheduled.

## Consequences
- Abandoned Link sessions cost nothing. Lost callbacks cost one `unknown` slot until the owner
  clears it.
- Tests: the guard at 7, 8, 9 and 10. Deleting a connection doesn't change the count. Two
  concurrent reservations leave exactly one. A sandbox reservation is not counted. A relink
  reserves nothing. An 11th request is refused before any Plaid call.
