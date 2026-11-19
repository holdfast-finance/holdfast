# Round table 05: Prism (frontend, brand) + Beacon (docs, sites)

Observed: `web/src/pages` has only `auth/`, `settings/`, Overview and a PlaceholderPage. MSW is already wired (`mocks/{db,handlers,browser,node}.ts`, msw ^2.15). Types are hand-written (`api/types.ts`, TODO(openapi) in `client.ts`). No openapi-typescript, no vite-plugin-pwa, no visual or axe tooling. E2E is one Playwright spec (`first-run`). Marketing sites are static (`holdfast-site`) or a bare Vite dist (`holdfast-cloud-site`).

## 1. Position
1. **Generate types from OpenAPI now (before Phase 2), and make the contract the sync point.** Phase 2 adds roughly 25 endpoints. Hand-writing types for them guarantees drift on money fields (string vs number). Make the committed `web/src/api/schema.d.ts` the artefact a CI job regenerates and diffs, so a backend change that alters the contract fails the web build. This beats any discipline-based rule.
2. **Build each page contract-first, in the same phase as its backend, but in a separate run after the router's OpenAPI is merged.** Backend run lands the router and schema, a Prism run builds the page against MSW handlers derived from that schema, and Sounding then runs e2e against the real stack. Do not build pages ahead of the backend (the schema will move) or leave them to the end (Phase 9 becomes a UI crunch). The exception is Accounts, Sync Health and Item slot counter, which ship in Phase 2 because they are the owner's only visible proof Plaid works.
3. **Brand-theme plumbing goes first this quarter, and as one S run.** It is small, it is queued, and every page after it would otherwise need re-verification in two themes. Add the AA contrast test for both themes in the same run.

Order change: none to the phases. Move **PWA shell (manifest, icons, app-shell-only SW, no API caching)** forward to the Phase 2 window as an S run. Hard rule 12 is easiest to prove while the app is small, and later pages inherit the guard. Full 8.5 (swipe cards, install UX, private-access docs) stays where it is.

## 2. Epics and stories
**E5.1 Foundations**
- E5.1a Brand-theme plumbing (`data-brand`, Holo tokens, fonts, AA test both themes) — S — `tokens.node.test` passes for signal+holo; theme switch via env/household setting — none
- E5.1b openapi-typescript + openapi-fetch; `make types` dumps the schema offline (no running stack: `app.openapi()` script), CI drift check — M — hand-written `types.ts` deleted; `pnpm typecheck` fails on a backend contract change — Harbor for CI
- E5.1c MSW handlers generated from schema fixtures (one `fixtures/` module shared by unit, Playwright and dev) — S — same fixtures drive vitest and e2e — E5.1b
- E5.1d Visual regression baseline (see Risks) — M — Playwright screenshots of shell, login, Overview in both brands, light/dark, at 375 and 1280 — E5.1a
- E5.1e PWA shell, no API caching — S — Lighthouse PWA installable; test asserts SW has no `/api` route — none

**E5.2 Pages by phase** (each M unless noted, plus S review + S acceptance)
- E5.2a Phase 2: Accounts + Item slot counter + Sync Health + Plaid Link flow, with 8/10 typed-confirm dialog — L — slot guard UI cannot create an 11th; sandbox flow in e2e — Tide router
- E5.2b Phase 3: Documents (upload, parse status, PDF beside fields) + import wizard + reconcile `needs_review` queue — L — a reconciled and an unreconciled synthetic statement render — Tide
- E5.2c Phase 4: Transactions (virtualised, filters, bulk, split, rules, review mode mobile) — L — 50k synthetic rows scroll at 60fps in CI budget — Ledger
- E5.2d Phase 5: Budgets, Forecast (P10/50/90), Dashboard v1, alerts — L — Dashboard works at 375px — Ledger
- E5.2e Phase 10a Investments/crypto holdings (read-only) — M
- E5.2f Phases 6, 7, 8: Credit, Taxes (behind `FEATURE_TAX` at route level), Advisor (streaming, tool-call transparency) — M each
- E5.2g Phase 8.5 mobile: swipe cards, bottom nav polish, install UX — M

**E5.3 Docs and sites (Beacon)**
- E5.3a Docs site on Astro Starlight in `docs-site/` (static, no service, so no ADR needed beyond a short note), sourced from `docs/user/` — M
- E5.3b OSS-milestone doc set: Quickstart (10-minute Tailscale install), Concepts (RLS, households, visibility), Connect Plaid/SimpleFIN, Import guide with a per-bank profile page generated from `profiles/*.yaml`, Backup/restore, Security model, Contributing, Tax disclaimer — L
- E5.3c Generated reference: OpenAPI HTML (from E5.1b schema), env var table from `.env.example`, feature flags table — S
- E5.3d Marketing sites: add real screenshots from E5.1d fixtures; keep Cloud preview waitlist-only until Safeguards — S each

## 3. Risks
- **Visual regression flake.** Use Playwright `toHaveScreenshot` inside the same pinned Docker image locally and in CI, fonts self-hosted (already fontsource), animations disabled, clock and data frozen via MSW fixtures. Snapshot only ~12 key states, never dense tables with live data. Reject Chromatic/Percy (new external service, rule 9). Storybook not worth the token cost.
- **Money-as-string drift:** generated types make `number` for NUMERIC impossible only if the backend emits `string` with a pattern in the schema. Ask Ledger for `Decimal` fields annotated `format: decimal`, and add a lint test that fails if any generated schema field named `*amount*|*balance*` is `number`.
- **Contract churn:** require `schema.d.ts` diff in every phase PR; review it as the API change summary.
- **PWA caching real data (rule 12):** unit test on the generated SW config plus a Playwright offline test showing the shell loads and API calls fail.
- **Cloud brand leaking into self-host:** ui-brand-reviewer checks both themes; token-purity grep in CI for `bg-signal|text-holo` etc.

## 4. Challenge
- PLAN 11.1 says Zod schemas "generated from OpenAPI". That adds a second generator (orval or openapi-zod) for little. Hand-write zod only for forms, infer shared field constraints from the generated types, and let the server validate. Drop generated zod.
- The brief treats the public site as a Phase 9 item. Docs are the open-source milestone's real deliverable and are cheapest if written per phase (one S docs run at each phase review), not in a Phase 9 crunch. Make "user doc page exists" an acceptance line for every phase.
- Magic UI / Aceternity on Dashboard are optional polish. Defer them until after Phase 5; they cost tokens and need reduced-motion handling.

## 5. Token savers
- One shared `fixtures/` module and one `PageShell` + `DataState` (loading/empty/error) component, so each page run is only columns and queries.
- Give page runs only the schema slice (`schema.d.ts` paths for that router) plus the PLAN §11 row, not whole files.
- Screenshot only on a `pnpm test:visual` command, not in the default test run; update baselines in one batch per phase.
- Generate bank-profile docs and the env/flag reference from source files, not by hand.
- Reuse the existing `ui-brand-reviewer` once per phase on the diff of `web/` only.
