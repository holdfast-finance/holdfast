# 0023. Plaid setup is guided and self-verifying, not automated

- Status: Accepted
- Date: 2026-11-21
- Deciders: Keel (Plaid lane). The owner sees it in the weekly digest.

## Context
A self-hosted household brings its own Plaid account (PLAN 17.1, 17.4). Someone who clones the repo
has to get from `make up` to a synced bank, and most of that path runs through Plaid's dashboard.
The question was whether Holdfast should do the Plaid setup for them, hand them a list of steps, or
something else. What Plaid allows (checked against plaid.com/docs on 2026-11-21) settles most of it:

- **No API creates a Plaid account or applies for a plan.** Sign-up, email verification and the
  Trial plan application are dashboard-only. Plaid's Developer Policy forbids sharing keys, so the
  project cannot ship any.
- **Trial plan:** 10 production Items, and `/item/remove` does not give a slot back. The eleventh
  fails with `TRIAL_CONNECTION_LIMIT`. Trial covers Transactions, Auth, Balance, Identity, Assets,
  Investments, Liabilities and Statements, and reaches almost all OAuth institutions without the
  compliance-centre registration that paid Production needs.
- **No API lists a client's enabled products or its Items.** `/link/token/create` refuses a product
  the account lacks with `INVALID_PRODUCT`, and refuses a redirect URI missing from the dashboard
  allowlist with `INVALID_FIELD`. A Link token creates no Item and costs nothing.
- **`/institutions/get` is free** and fails with `INVALID_API_KEYS` for a bad or wrong-environment
  key pair.
- **A redirect URI is optional on desktop web** (OAuth finishes in a pop-up). Phones and webviews
  need one. Production URIs must be HTTPS; `http://localhost` works in Sandbox only.

So the manual steps cannot be removed, but nearly every one can be *checked* by asking Plaid.

## Decision
1. **Holdfast does not automate Plaid sign-up and never ships keys.** The person creates the account
   and applies for the Trial plan themselves.
2. **Every manual step is verified live.** `POST /api/plaid/setup/check` (owner only) probes the
   keys, each configured product and the redirect URI, and returns an ordered checklist:
   `keys_present`, `keys_valid`, `products`, `redirect_uri`, `item_slots`. Each step carries a state
   and a machine-readable reason (Plaid's `error_code` where there is one) plus Plaid's
   `request_id`. `GET /api/plaid/setup` returns the same checklist from configuration alone.
3. **A product the plan lacks is switched off, not fatal.** Setup is ready when at least one
   product is enabled. `balance` is never sent in `products`; every Item has it.
4. **Sandbox comes first.** `PLAID_ENV` stays `sandbox` by default. The checklist is the same in
   both environments, and a later story adds a one-click Sandbox test link so the whole pipeline is
   proven before a real slot is spent.
5. **Slots are counted locally**, per household, from `bank_connection`. At 8 the checklist warns;
   at 10 it blocks. Plaid offers no count to compare against, so P4 keeps a tombstone for removed
   Items.
6. **Probe tokens are discarded.** Link tokens minted by a check never leave the service: not
   returned, stored, audited or logged. The slot guard in `.claude/rules/plaid-and-connectors.md`
   applies to tokens handed to Link; a probe cannot create an Item.
7. **Keys still come from one source (ADR 0015).** Self-host reads `.env`. This ADR does not add
   in-app key entry for self-host.
8. **The client is a thin `httpx` wrapper, not `plaid-python`.** The SDK parses numbers as `float`;
   hard rule 1 needs `Decimal` from the first byte. `httpx` moves from a dev to a runtime
   dependency (BSD-3-Clause, maintained, already in the lockfile). Keys go in headers, never the
   body; Plaid's free-text `error_message` is dropped because it can echo request fields.

## Alternatives considered
- **A static checklist in the docs.** Cheap, but the person finds out a step was wrong only when
  Link fails, with Plaid's error and no pointer to the fix.
- **Holdfast-operated Plaid keys for self-hosters.** Against Plaid's policy, costs per Item, and
  puts every self-hoster's bank data behind the project's account (PLAN 17.3).
- **Browser automation of Plaid's dashboard.** Brittle, needs the person's Plaid password, and is
  exactly the scraping the project refuses elsewhere.
- **In-app key entry for self-host.** Friendlier than editing `.env`, but ADR 0015 chose a single
  source per deployment so a stored row can never silently replace the operator's key. Left for
  Lattice and Bulwark to reopen if the `.env` step proves to be where people give up.
- **`plaid-python`.** Official, but float parsing would need a post-hoc repair of every amount.

## Consequences
- The Connections page (P9) renders this checklist and owns its copy; the API returns ids only.
- SimpleFIN and statement import stay first-class paths for people who do not want a Plaid account.
- A check makes up to `1 + products + 1` Plaid calls, so it is limited to 6 per 5 minutes per
  household. Sandbox allows ten `/institutions/get` a minute.
- When ADR 0015's resolver merges, `plaid_credentials()` in `app/services/plaid_setup.py` is the one
  function that changes.
- Follow-ups: Sandbox test link and Link token/exchange with the slot guard (P4), a
  `make plaid-keys` prompt so nobody hand-edits `.env` (Harbor), the Connections page (Prism).
