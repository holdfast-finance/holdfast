# 02 — Bulwark (security)

## 1. Position
1. **Run a security gate before Phase 2 sees a real token, not in Phase 9.** Close Phase 1 lows 1–2, pull M3/L5/Redis auth/`__Host-` cookie forward from Phase 9, and add Fernet key versioning with a rotation drill. Unpinned actions are a supply-chain path straight to `main`.
2. **Make CI the reviewer of record.** `pip-audit`/`pnpm audit` are `continue-on-error` today. Under autonomy, a control that doesn't fail the build doesn't exist. Hand review is for design and new trust boundaries, not grep-able rules.
3. **Guard the autonomy model itself.** Agents merge and push while `.env` holds **production** Plaid keys. Guardrails must be mechanical (E1.4).

**Order change:** E1 runs as "Phase 1.5" beside brand plumbing and hard-blocks Phase 2 Plaid work.

## 2. Epics + stories
**E1 Pre-Phase-2 security gate**
- E1.1 Argon2 semaphore before DB session + real-client-IP check — S — burst test keeps pool free; `audit_log.ip` shows non-docker addresses on an isolated stack — none
- E1.2 `SecretToken` type (no `repr`/`str`/JSON, decrypt only inside `integrations/`) + Fernet `kid` versioning + `make rotate-keys` — M — rotation test re-encrypts all rows; log-canary test passes — none
- E1.3 Move up from Phase 9: SHA-pinned actions, digest-pinned images, Redis auth, IDs-only arq payloads (enforced by a test), `__Host-hf_session` — M — `zizmor`/pin-check green; payload test rejects non-UUID args — none
- E1.4 Autonomy guardrails — M — see §3; protected-path CI check live; `main` branch protection requires every check — none

**E2 CI security controls (automate, don't review)**
- E2.1 Make audits blocking (high and above), with an expiring allowlist file — S — a seeded vulnerable dependency fails CI — none
- E2.2 Semgrep custom rules: no `float` near money; no logging of `*token*|*secret*|access_url`; no f-string SQL / `text(f"...")`; agent tools may only touch `v_*`; no `PLAID_ENV=production` literal outside the slot guard — M — each rule has a failing fixture — none
- E2.3 Log-canary test: canary token/SSN/account number through every connector/LLM path never reaches logs, errors, responses or OpenAPI — S — green — E1.2
- E2.4 Trivy image scan, actionlint + zizmor, license allowlist (AGPL-compatible) — S — blocking — E1.3
- E2.5 Route-enumeration test: every non-public route needs session + CSRF, sensitive ones step-up — S — undecorated route fails CI — none

**E3 Per-phase threat models** (one page each, written before kickoff and diffed after) — S each
- P2: token at rest/in logs, SimpleFIN URL is a bearer credential (+SSRF), slot-guard bypass, agent creating prod Items.
- P3 documents: traversal, PDF/zip bombs, parser in a capped worker, per-file encryption with its own key.
- P8 LLM: property-based redaction tests per provider, injection via merchant names, MCP scope, SSRF on model URLs.
- P10a/c crypto: xprv/seed detector on every input, exchange-key permission check, PSBT audit, spend hard-off in hosted builds.

## 3. Risks and autonomy guardrails
- **Agent with prod keys.** Agent runs get a `.env.agent` that contains only sandbox keys. The production Plaid path needs a key that exists only on the host, so agents can't reach it.
- **Agent weakening its own gates.** Diffs to `.github/`, `.claude/`, `CLAUDE.md`, `.githooks/`, `app/security/`, RLS migrations or `guardrails.py` need a separate Bulwark verdict file; CI fails on deleted/skip-marked security tests. A weekly async digest goes to the owner queue.
- **Bad push to `main`.** Branch protection, no force-push, signed tags, one `scripts/release` merge path that refuses unless green, one-command rollback.
- **Runaway spend/actions.** Per-window token cap. No agent may run `gh repo`, DNS, Plaid dashboard or Tailscale ACL commands (deny list in `settings.json`).
- **Real IP masking (low 2).** Retire it early with a spike on the target host type.

## 4. Challenge
- **Plaid webhooks vs. Tailscale-only networking.** Rule 12 forbids public ingress, so Plaid webhooks can't arrive. Phase 2 must be **poll-first** (scheduled `/transactions/sync`); webhooks only via a Funnel/relay ADR. Otherwise Sync Health lies.
- **"Phase 9 hardening" is the wrong bucket under autonomy.** Supply-chain and CI integrity are prerequisites for agents pushing.
- **17.3 Safeguards program** starts now as `docs/security/program.md` (risk register = these threat models), not at hosted launch.

## 5. Token savers
- Semgrep/tests take the mechanical classes, so Bulwark reads only protected-path or new-boundary diffs (S, not a full-phase re-review).
- Threat models double as review checklists. Scout (haiku) pre-sweeps, and opus reads only the flagged hunks.
- Blocking audits with an allowlist end per-phase audit triage.
