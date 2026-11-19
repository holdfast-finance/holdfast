# Tide: integrations memo (Phase 2 + 3)

## 1. Position
1. **Build the connector layer fixture-first, so the owner's Plaid Trial is never on the critical path.** `BankConnector` (`link/sync/relink/health`) gets a `FakeConnector` and a `ReplayPlaidClient` (recorded JSON) before any live call. Plaid sandbox (`user_good`, `user_transactions_dynamic`, `/sandbox/item/reset_login`, `/sandbox/public_token/create`) is the only live path in CI-adjacent runs. Real Items are a final, owner-queued smoke.
2. **One shared ingest core for every source.** Plaid sync, SimpleFIN, CSV/OFX/PDF and Mint/YNAB/Monarch/Copilot all emit `NormalizedTxn` into one `ingest_transactions(account, batch, source)` service. Sign negation, `Decimal` parsing, fingerprinting, dedupe and the reconcile check live there once. This is where the money bugs would otherwise multiply.
3. **Do the schema gaps first (one migration, Ledger reviews).** The model has `external_id`, `import_fingerprint`, `pending_external_id`, `tx_cursor` and unique constraints on `(account_id, external_id)` and `(account_id, import_fingerprint)`. It lacks:
   - `transaction.superseded_by` (PLAN 8.1 needs it);
   - a `statement` / import-batch table (opening, closing, period, `reconcile_status`, `document_id`), since reconcile has no home today;
   - `connector_sync_run` (start, end, added/modified/removed counts, error code, `request_id`) for Sync Health;
   - `account.external_id` and `bank_connection.sync_lock`.

**Order change:** build the competitor importers (CSV, no bank quirks) before PDF profiles. They share the pipeline, need no owner data, and prove dedupe early. PDF/bank profiles come last in Phase 3 and stay gated on real sanitized samples.

## 2. Epics and stories
**E2.1 Connector foundation**
- E2.1a BankConnector protocol, registry, per-household BYO-keys config, Fernet token helper — S — contract tests pass for a fake connector — none
- E2.1b Migration for the schema gaps above, with RLS and up/down test — S — new-migration checks pass — none
- E2.1c `ingest_transactions` service (upsert on `(account_id, external_id)`, sign handled by caller, audit row) — M — idempotent on replay; 100 random batches give the same end state — E2.1b

**E2.2 Plaid**
- E2.2a Client wrapper with `parse_float=Decimal`, `request_id` logging and a token-redaction test — S — no float in the parse path (grep test) — E2.1a
- E2.2b Link token, exchange, Item slot counter and guard (8 typed confirm, never 11th) — M — guard unit-tested at 7/8/10; deleted Items still count — E2.2a
- E2.2c Transactions sync loop: cursor saved after commit, mutation-during-pagination restart, `removed`, pending→posted — M — replay fixtures; kill mid-page and resume gives no dupes — E2.1c, E2.2a
- E2.2d Balances, investments and liabilities → snapshots/holdings (sign test per product) — M — fixtures; one test per product — E2.2c
- E2.2e `ITEM_LOGIN_REQUIRED` → `needs_relink`, update-mode Link, backoff — S — a sandbox reset-login flow reaches `needs_relink` and recovers in the same slot — E2.2b
- E2.2f Scheduler (poll 4–6h) and `seed_sandbox.py` — S — worker test with a fake clock — E2.2c

**E2.3 SimpleFIN** (needs an ADR for the new service)
- E2.3a Claim token → access URL (encrypted), `/accounts` sync, balances — M — recorded-response tests — E2.1c
- E2.3b SimpleFIN quirks: no pending, a 90-day window, per-account errors in `errlist`, synthetic IDs — S — fixtures — E2.3a

**E2.4 Sync Health**
- E2.4a `/api/sync-health` (per-account status, last success, stale > 48h, error code) — S — OpenAPI string amounts; stale flag unit-tested — E2.1b
- E2.4b Duplicate cleanup after relink (a new Item re-delivers history under new IDs) — M — merge by `(account, date, amount)`; Plaid wins; reversible via `superseded_by` — E2.2c
- E2.4c Web page (Prism) — M — 375px layout, relink button — E2.4a

**E3.1 Pipeline**
- E3.1a `NormalizedTxn`, fingerprint with same-day ordinal, per-statement reconcile (±0.01) — M — property tests — E2.1c
- E3.1b `dedupe.py` vs Plaid (±3 days, rapidfuzz ≥ 85, Plaid wins, `superseded_by`) — M — overlap fixtures — E3.1a
- E3.1c Upload endpoint, SHA-256 file dedupe (`document` already unique), `needs_review` review API — S — re-upload returns the same document — E3.1a
- E3.1d Review UI for failed reconciles — M — Prism — E3.1c

**E3.2 Importers**
- E3.2a Generic CSV with a YAML profile loader — M — E3.1a
- E3.2b OFX/QFX (FITID becomes `external_id`) — S — E3.2a
- E3.2c Mint, YNAB, Monarch, Copilot CSV, one run each — S×4 — each re-import gives zero new rows — E3.2a
- E3.2d PDF text extractor plus profile engine (Current, NFCU, Wells Fargo), synthetic PDFs from templates — L — reconcile 100% on fixtures — E3.1a
- E3.2e Entry-point plugin hooks — S — a dummy plugin is discovered — E3.2a

## 3. Risks and early retirement
- **Plaid sandbox is too tidy** (no pending→posted churn, no mutation errors). Retire with hand-built replay fixtures for each edge case, in a spike run first.
- **Fingerprint drift.** If normalization changes, every re-import duplicates. Version the normalizer inside the hash input (`v1|...`) and keep golden hash tests.
- **Same-day identical rows** (two $4.50 coffees). The ordinal is per `(account, date, amount, desc)` within one statement. Cross-source it can mismatch, so dedupe matches by multiset count, not by single row.
- **Sign inversion** is applied only in `integrations/plaid/`. SimpleFIN and OFX are already outflow-negative. One test per source.
- **Real PDF layouts differ from synthetic ones.** Ship PDF profiles with a `provisional` flag. Ship CSV/OFX as the supported path.
- **Rate limits and cost.** Sandbox is free, but cap calls per CI run.

## 4. Challenge
- PLAN 7.2 says "map pending→posted via `pending_transaction_id`" as a mapping. Treat it as **delete the pending row and insert the posted one in one transaction**, keeping the category and notes. Otherwise rules and splits on the pending row are lost.
- PLAN 8.1's ±3-day fuzzy match is too loose to run automatically on recurring same-amount charges (subscriptions, rent). Auto-link only when the match is unique. Otherwise queue it for review.
- The "4–6 days" for Phase 3 hides the fact that PDF parsing is the long pole and cannot be verified without real samples. Split it off as shown above.

## 5. Token savers
- Record Plaid responses once with a script into `tests/fixtures/plaid/*.json`. Agents replay them and never re-read the Plaid docs.
- One `NormalizedTxn` golden file per importer. New importers are copy-and-edit runs (the `bank-profile` skill).
- A single `make test-integrations` target, so agents don't run the whole suite (532 tests).
- Keep the sync-loop tests table-driven (page sequences as data), so extra edge cases cost one line each.
- Don't read the Plaid OpenAPI spec. Pin `plaid-python` and use the types.
