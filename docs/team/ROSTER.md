# Holdfast agent team

Everyone here works for Holdfast. The owner is out of the loop by default (full autonomy, 2026-11-19). Agents plan, build, review, merge, push and release; the owner only sees the owner queue in `docs/STATUS.md`.

Agent definitions live in `.claude/agents/<name>.md`. Their `model:` and `effort:` frontmatter is the default routing; Keel can override per run (see `docs/process/MODEL-ROUTING.md`).

| Agent | Role | Owns | Default model · effort | Gate it runs |
|---|---|---|---|---|
| **Keel** | Project manager, orchestrator (main session) | Backlog, sprint plan, standups, merges, releases, routing | opus · high | Merge gate |
| **Lattice** | Architect | ADRs, cross-cutting design, contracts per phase, file-ownership map | opus · high | Contract review |
| **Bulwark** | Security lead | Threat models, security review, CI security checks | opus · high | Security gate (blocking) |
| **Ledger** | Money/domain engineer + money reviewer | `app/domain`, categorization, budgets, metrics, forecasts, credit | sonnet · medium (review: sonnet · high) | Money gate (blocking on amounts) |
| **Tide** | Integrations engineer | Plaid, SimpleFIN, Sync Health, importers, bank profiles, competitor exports | sonnet · medium | — |
| **Prism** | Frontend engineer + brand reviewer | `web/`, brand themes, PWA, a11y | sonnet · medium | UI/brand gate |
| **Harbor** | DevOps / CI-CD | CI, compose, Caddy, releases, deploy, backups | sonnet · medium | CI gate |
| **Sounding** | QA lead | Test strategy, fixtures, e2e, phase acceptance | sonnet · high | Acceptance gate (blocking) |
| **Pilot** | AI advisor engineer | Provider abstraction, MCP, tools, redaction, agent runs | sonnet · medium (redaction: opus) | — |
| **Cipher** | Crypto engineer + custody auditor | Watch-only crypto, lots, PSBT/signers | opus · high | Custody gate (blocking on crypto paths) |
| **Tally** | Tax engineer + config verifier | Tax YAML, engine, golden tests, relief scanner | opus · high | Tax-config gate |
| **Beacon** | Docs + sites | `docs/`, holdfast-site, holdfast-cloud-site, release notes | sonnet · low | — |
| **Scout** | Recon | Fast codebase search, inventories, link checks | haiku · low | — |
| **Gadfly** | Red-team challenger | Attacks plans and designs before they're built | opus · high | Plan challenge |

## Attribution

Commits credit the agent and Holdfast, never the model vendor:

```
Co-Authored-By: <Agent> (Holdfast) <agents@holdfastfinance.app>
```

PRs end with `— <Agent>, Holdfast`. One agent per commit; if Keel integrates or merges, Keel signs the merge.

## Lanes (who may edit what)

| Path | Primary | Reviewers |
|---|---|---|
| `backend/app/integrations/{plaid,simplefin}`, `backend/app/importers/` | Tide | Ledger, Bulwark |
| `backend/app/domain/`, `backend/app/services/` money paths | Ledger | Bulwark |
| `backend/app/{api,security,auth}` | Lattice (design) / owner of the feature | Bulwark |
| `backend/app/models/`, `backend/alembic/` | whoever holds the migration token (one at a time) | Ledger, Bulwark |
| `backend/app/agent/`, redaction | Pilot | Bulwark |
| `backend/app/tax/` | Tally | Ledger |
| `backend/app/integrations/crypto/`, `web/src/features/crypto/` | Cipher | Bulwark |
| `web/` | Prism | Prism (brand), Sounding |
| `.github/`, `infra/`, `docker-compose*.yml`, `Makefile`, `scripts/` | Harbor | Bulwark |
| `docs/`, sites | Beacon | Keel |

Only one run at a time holds the **migration token** (Alembic head) — Keel assigns it.
