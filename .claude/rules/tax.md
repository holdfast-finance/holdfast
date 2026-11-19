---
paths:
  - "backend/app/tax/**"
  - "backend/app/agent/prompts/**"
  - "backend/tests/test_tax*"
  - "web/src/pages/Taxes.tsx"
---
# Tax module

- Feature-flagged: everything here is behind `FEATURE_TAX` and must import cleanly when the flag is off. Core modules must not import from `app.tax` (one-way dependency).
- Every numeric value in `config/<year>.yaml` and `relief_catalog.yaml` carries a `source:` (IRS Rev. Proc., Pub, form instruction or IRC section, plus URL) and the tax year. Unknown → `TODO_VERIFY`. Use the `tax-year-config` skill, and have `tax-config-verifier` check the result.
- Never hardcode a bracket, limit, threshold, phase-out or rate in Python. Read it from config.
- Check effective dates. Provisions expire and OBBBA (2025+) added new ones. Never assume a credit still exists.
- Engine output is a line-item trace, where every number links to its source documents and config keys.
- Golden tests come from IRS worked examples (cite the publication and page in the test docstring).
- Risk labels: 🟢 standard / 🟡 documentation-heavy / 🟠 scrutinized / 🔴 won't recommend. Evasion is always 🔴 and declined with the reason.
- User-facing copy never says "CPA". It's "tax-prep and planning". The user self-files, and the app never transmits to the IRS.
- IRC §7216: tax data is used only for the user's own return. Never mix it into analytics, telemetry or other features.
