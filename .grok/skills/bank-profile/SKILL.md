---
name: bank-profile
description: >
  Add or fix a statement-import bank profile
  (backend/app/importers/profiles/<bank>.yaml) or a competitor-export importer
  (Mint, YNAB, Monarch, Copilot CSV). Use for "add support for BANK statements",
  "import fails for BANK", or a new CSV or OFX format. Trigger: /bank-profile.
---

# Bank profile / importer

Input is a sanitized sample the owner placed in `backend/tests/fixtures/statements/<bank>/` (fake names, masked account numbers). If the only file is a real statement, stop. Never copy a real statement into the repo.

1. Inspect the sample. Identify the text fingerprint, the date format, the sign convention, the opening and closing balance lines, and multi-page table continuation.
2. Write `profiles/<bank>.yaml`: `fingerprint` regexes, `table` region and columns, `date_format`, `sign` (`as_is`, `invert`, or `debit_credit_columns`), `opening_balance` and `closing_balance` regexes, and `skip_rows`.
3. Competitor CSVs: add a column map, with sign and category mapping. Unknown categories become Uncategorized.
4. Tests:
   - the fixture parses to the expected transaction count
   - opening plus the sum of transactions equals closing within $0.01
   - a second import creates zero new rows
   - overlap with Plaid fixtures dedupes, and Plaid wins
   - a known debit is negative
5. Spawn `money-invariants-reviewer` on the diff. Commit: `feat(import): <bank> profile`. Tide does not merge.
