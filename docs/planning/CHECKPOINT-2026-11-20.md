# Keel checkpoint · 2026-11-20 (owner stop + usage limit)

`main` is at `0633604`. Nothing was merged after this point. Only Keel merges.

## Merged this window (all gated: ci-required + review-gate, SHA-bound sign-off)
#18 F10b `d031e54` · #21 alpha metadata `8ac0e67` · #17 F8b `6bee359` · #19 F5a `9e16a5c` · #20 ADR 0015 `f073ceb` · #22 F3 `df96bdb` · #23 F3b `d8694f8` · #25 invite atomic `e9978f5` · #24 F2 jobs `193fe49` · #26 F10c `0633604`

## Open, resume in this order
| Item | State | Next action |
|---|---|---|
| #27 F3c MoneyStr/PriceStr/QuantityStr @ `1d3f075` | money-invariants **APPROVE** at `1d3f075` (800k-value fuzz; non-blocking: quantize computed response amounts to 0.01 HALF_EVEN at the presentation boundary when the first response schema computes money) | land (not a protected path) |
| #16 F7b free-tier main-guard @ `b27beb6` | Harbor fixed Bulwark's empty-sibling-listing HIGH (63 tests) | Bulwark re-review with its own dry.py incl. the empty-listing negative; then land |
| `feat/S0-statement-sanitizer` @ `f7424ec` (no PR) | merges clean; protected `backend/pyproject.toml` (+pdfplumber, reportlab) | **Privacy concern:** amounts are scaled by a secret int 3–9, so real amounts are likely recoverable. Rework to independent synthetic amounts that still reconcile before review. Also pyright: `cand` possibly unbound `sanitize.py:268`. Then PR + Bulwark |
| `feat/F5c-secret-resolver-rotation` @ `61c005f` (no PR) | 700 tests on old base; **conflicts with main** (auth.py still uses crypto.encrypt/decrypt on main) | Harbor merges origin/main, re-runs suite; then PR + Bulwark (security/, docker-compose.yml, conftest) |
| `feat/F6-import-sync-runs` (Lattice, migration 0005) | in flight at stop | check branch; PR + Bulwark (RLS migration) |
| `fix/F2b-job-hardening` (Helm/Grok, worktree `~/.grok/worktrees/holdfast-invite`) | in flight at stop; output `scratchpad/helm-f2b-result.md` | verify pushed commit vs worktree, PR + Bulwark |
| `docs/github-release-tags` | stacks on #21 (merged) | open PR |
| Dependabot #11–#14 | green CI, protected paths | after #16: update branch + Bulwark |
| #8 Codex/Grok onboarding | CONFLICTING; local uncommitted harness in main checkout (`.codex/ .grok/ .agents/ AGENTS.md ROSTER CODEX-SETUP GROK-SETUP CHECKPOINT WORK-ORDERS`) | reconcile into one PR, Bulwark |

## Follow-ups queued
F7c sandbox `ci-local` (env allowlist, container) · invites: note standalone-Redis-only on Lua scripts, cache `register_script` · Phase 2 carry: use MoneyStr/PriceStr for every money field, enqueue binding (F2b) · F5c lows per ADR 0015 review.

## Owner
- Move the statements zip into `data/inbox/` (0600) and shred the `/tmp` copy: see the command in the session. Agents never read it. Sequence: S0 sanitizer → F6 → I1 → bank profiles from owner-reviewed sanitized output.

## Tooling notes
- Landing helper (update branch, prove normalized diff equals the reviewed diff, relabel, wait, squash with `--match-head-commit`) lived in the session scratchpad as `land.sh`; recreate if lost.
- Codex/Grok can't write worktree git refs; Keel commits/verifies their work (see memory `driving-codex-grok-headless`).
