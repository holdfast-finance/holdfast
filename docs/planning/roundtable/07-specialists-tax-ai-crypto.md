# 07 — Specialists: Tally (tax), Pilot (AI), Cipher (crypto)

## 1. Position
1. **Build the pure cores now as a background lane** (~1 run per window, beside Phases 2–5). The tax engine, redaction, `LLMProvider`, descriptor parsing and the lot engine take typed inputs and need no Plaid, imports or categories. The tax/agent/document tables already exist from Phase 1. Wiring and UI stay where the plan puts them.
2. **TY2025 proves the engine; TY2026 is the product.** TY2026 form instructions and their worked examples won't publish until Dec–Jan. TY2025 examples exist today, and the TY2026 Rev. Proc., COLA and HSA values can already be sourced. Filing season opens inside this quarter.
3. **Get Phase 7/8 acceptance off owner documents** by using synthetic returns and independent oracles (§3).

**Order changes:** move the HSA shoebox (core per §20.3) to right after Phase 4. It needs only the `medical` tag, the existing document vault and a ledger. Split 10c so PSBT and descriptor logic comes early and devices come last.

## 2. Epics + stories
**E7 Tax (Tally)**
- E7.1 TY2025 + TY2026 federal YAML, sourced, plus the TODO_VERIFY gate — M — tax-config-verifier clean; 2025 gate green — none
- E7.2 Engine core with line trace — L — matches IRS 1040-instruction/Pub 17 worked examples to $0 — E7.1
- E7.3 Synthetic return corpus (≥ 25) + oracle diff harness — M — every profile within $1 of the oracles — E7.2
- E7.4 State plugin interface, SC first (state from household settings) — M — SC DOR examples match — E7.2
- E7.5 Manual tax_item entry + verify UI — M — no `tax_item` without an explicit verify — Phase 1
- E7.6 HSA shoebox: receipts, reimbursable balance — M — Decimal balance reconciles to receipts − reimbursements — Phase 4
- E7.7 Relief catalog v1 (12 highest-value entries) — M — each sourced, with years_effective — E7.1

**E8 Advisor (Pilot)**
- E8.1 Redaction library — S — property tests + 1k synthetic strings, zero leaks — none
- E8.2 `LLMProvider` (anthropic/ollama/openai_compatible) + `FakeProvider` cassettes — M — redaction enforced in the base class — E8.1
- E8.3 Tool registry, `v_*` whitelist, row limits — M — fuzzed base-table access rejected — Phase 4 views
- E8.4 Read-only MCP server on the same registry — M — Bulwark pass; RLS holds per person — E8.3
- E8.5 Eval harness on the seeded household — M — 20 golden questions scored deterministically — E8.3
- E8.6 Chat UI + findings inbox + monthly run with token hard stop — M — Prism pass; budget stop audited — E8.2

**E10 Crypto (Cipher)**
- E10.1 Descriptor/xpub parse + derivation (BIP 32/44/49/84/86, multisig) — M — BIP + Core `deriveaddresses` vectors pass — none
- E10.2 §18.5 migration — S — RLS on every table — Phase 1
- E10.3 Per-wallet lot engine (FIFO/spec-ID, 8949 boxes G–L) — M — hand-computed fixtures incl. noncovered transfer-ins — E10.2
- E10.4 Transfer matching + CCXT/CSV import with read-only key check — L — ≥ 99% synthetic pairs matched; keys with trade or withdraw rights rejected — E10.3
- E10.5 PSBT build + QR(UR/BBQr) + file signer — L — regtest round trip — E10.1
- E10.6 1099-DA reconcile + 8949 CSV — S — mismatches flagged — E10.3, E7.2

## 3. Risks
- **No real docs.** Tax-Calculator and PolicyEngine-US are **test-only** oracles (license check + ADR) and never ship. Agents fill official blank IRS fillable PDFs to make synthetic W-2/1099s. Replace "last year's return within $1" with "≥ 25 synthetic returns within $1 of both oracles". The real-return check moves to the owner queue as non-blocking.
- **Invented figures.** A test fails on numeric literals in `backend/app/tax/` other than 0/1.
- **No API key.** `FakeProvider` cassettes run in CI and Ollama for local smoke runs. Anthropic runs stay flagged until the owner adds a key.
- **Redaction bypass by a new provider.** A test fails any provider subclass whose `send` skips redaction.
- **No devices.** Bitcoin Core regtest in a CI-only compose profile (ADR) stands in for signing. Physical Coldcard/Jade/Trezor signet runs go to the owner queue. A grep test fails on `xprv`/`tprv`/mnemonic lists outside test vectors.

**Models:** opus for E7.1–7.4 and 7.7 verification, E8.1, E8.4, E10.1, E10.3, E10.5. Sonnet for provider adapters, UI, CCXT/CSV, evals and the HSA vault. Haiku checks that source URLs resolve.

## 4. Challenges
- **§9.6 package is too big for Q1.** Ship the entry guide + workpapers XLSX. Defer the PDFs, paper-form filling, 1040-X look-back, 1095 parsers and OCR of scanned forms.
- **§10.1 runs Opus for the monthly review.** That's a hidden cost on strangers' BYO keys. Default to Sonnet with Opus opt-in.
- **"CPA agent" conflicts with hard rule 14.** Rename it to Advisor (`system_advisor.md`) before the strings spread.
- **SC is baked into §9.2/9.5.** Make it a per-household state plugin and ship federal + SC only.
- **10c:** QR + file this quarter. WebUSB waits until after 8.5. 10d stays cut.

## 5. Token savers
- Cache IRS source excerpts (value, page, URL) in `docs/tax-sources.md` so verifiers don't re-fetch publications.
- Generate fixtures from seeded scripts, not by hand in prompts.
- Pure cores test with `uv run pytest -k` and no compose stack.
- One shared cassette set across the E8 stories.
- Drafting on sonnet, verification on opus (≈ S each).
