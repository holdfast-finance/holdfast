# Security (always on)

- Secrets come from `.env` (never committed) or the encrypted per-household settings table. `.env.example` lists every variable with no values.
- Never read, print, cat or commit `.env`, `data/`, `secrets/`, real PDFs or exports. The guard hook blocks most of this, but don't rely on it.
- Auth: passkeys (WebAuthn) first, TOTP fallback, 12-hour sessions, re-auth for sensitive actions (adding an Item, export, package generation, PSBT build).
- Audit log every login, connector call type, export, agent run and settings change.
- New dependency → check it's maintained and the license is AGPL-compatible. `pip-audit` and `pnpm audit` run in CI.
- Data minimization: no SSNs, no full account numbers, no bank credentials (Plaid/SimpleFIN hold those).
- Telemetry: none by default. Opt-in only, and never containing financial data.
