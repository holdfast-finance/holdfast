---
name: bank-profile
description: "Add or fix a statement-import bank profile (backend/app/importers/profiles/BANK.yaml) or a competitor-export importer (Mint, YNAB, Monarch, Copilot CSV). Use for \"add support for BANK statements\", \"import fails for BANK\", or new CSV/OFX formats."
---

Apply the lane in `AGENTS.md` and the current SDLC. Return work outside your assignment to Keel with the evidence collected.

# Bank profile / importer

Inputs: a **sanitized** sample from the owner in `backend/tests/fixtures/statements/<bank>/` (fake names, masked account numbers, amounts are fine). If only a real statement exists, stop. Never copy a real statement into the repo.

1. Inspect the sample with `pdfplumber` (text + table regions) or by reading the CSV header. Identify the text fingerprint, the date format, the sign convention, the opening/closing balance lines and multi-page table continuation.
2. Write `profiles/<bank>.yaml`: `fingerprint` regexes, `table` region/columns, `date_format`, `sign` (`as_is` | `invert` | `debit_credit_columns`), `opening_balance` and `closing_balance` regexes, and `skip_rows`.
3. Competitor CSVs: add a column-mapping profile to `csv_generic.py`, with sign and category mapping (map their categories to ours, unknown → Uncategorized).
4. Tests (`test_importers.py`):
   - parses the fixture into the expected `NormalizedTxn` count,
   - **reconciles**: opening + Σ = closing ± 0.01,
   - re-import creates 0 new rows (fingerprint idempotency),
   - overlap with Plaid fixtures dedupes, with Plaid winning,
   - sign of a known debit is negative.
5. Run `money-invariants-reviewer` on the change. Commit: `feat(import): <bank> profile`.
