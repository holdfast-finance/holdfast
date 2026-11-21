# Security (always on)

- Secrets come from `.env` (never committed) or the encrypted per-household settings table. `.env.example` lists every variable with no values.
- Never read, print, cat or commit `.env` or `secrets/`. The guard hook blocks most of this, but don't rely on it.
- **Owner's real statements and exports (ADR 0019):** on the owner's own machine, Holdfast's own agents may read them to build and test importers and to load the owner's local instance. They live in a private directory **outside the repository** (mode `0700`), never under the repo tree. They never enter a commit, a PR or issue, a log line, a fixture, a Docker build context, a wheel or sdist, a release, or any service or tool outside the machine, including other vendors' coding CLIs. Report results as counts and pass rates, not as names, account numbers, merchants or amounts. Repo fixtures stay synthetic or sanitizer output (`make sanitize`).
- Auth: passkeys (WebAuthn) first, TOTP fallback, 12-hour sessions, re-auth for sensitive actions (adding an Item, export, package generation, PSBT build).
- Audit log every login, connector call type, export, agent run and settings change.
- New dependency → check it's maintained and the license is AGPL-compatible. `pip-audit` and `pnpm audit` run in CI.
- Data minimization: no SSNs, no full account numbers, no bank credentials (Plaid/SimpleFIN hold those).
- Telemetry: none by default. Opt-in only, and never containing financial data.
