# Keel checkpoint · 2026-11-25 (triad sprint 1)

First sprint under the triad (`docs/process/TRIAD.md`): Keel (Claude) leads and owns design, frontend
and merges. Anvil (Codex) and Helm (Grok) build full-stack in live owner-controlled sessions (ADR 0039).
The bus is `docs/comms/bus/`. The board is `docs/planning/TRIAD-BOARD.md`.

## Merged this sprint (every one gated: ci-required, review-gate, SHA-bound sign-offs)

| PR | Story | Built by | Gate |
|---|---|---|---|
| #93 | P9b-link follow-ups | Prism | Bulwark, Prism |
| #91 | C3b rules storage, migration 0012 | Ledger | Bulwark, Ledger |
| #94 | Triad protocol, board, bus | Keel | Keel (docs) |
| #97 | Work orders refresh | Anvil | Keel (docs) |
| #95 | ADR 0039 live vendor sessions, pre-push range scan, fc-delegate refuses `--allow-grok-write` | Keel | Bulwark (3 rounds) |
| #89 | H2 holdfast-instance and holdfast-sandbox, volume guard | Harbor | Bulwark (5 rounds) |
| #38 | Actions group bump | Dependabot | Bulwark |
| #98 | Phase 5 contract, ADRs 0040-0042 | Lattice | Keel |
| #37 | uv 0.13.9 | Dependabot | Bulwark |
| #99 | P9b-link-2 | Prism | Bulwark, Prism |

## Open, resume in this order

| Item | State | Next |
|---|---|---|
| #100 A1 account owner (Helm) | Bulwark APPROVE and Ledger PASS @ `fdc00b0` | merges when CI's DB tests are green; then A1b (bus brief) |
| #92 C3d RE2 engine | Bulwark round 7: allowlist parser holds (60k fuzz, 0 disagreements); one MEDIUM (4+ char literal prefix evades the probe, ~120 µs ceiling) | Ledger fixing (maximal literal runs, split messages), then Bulwark round 8 |
| #96 P2b ingest (Anvil) | Ledger and Bulwark APPROVE-WITH-NOTES @ `c429708`; Keel required 9 fixes and the interleaving property tests | branch now at `def771a` (pushed). Waiting on Anvil's `result` saying the round is complete, then re-review |
| `feat/I3-competitor-exports` (Anvil) | pushed at `3d54de8`, stacked on P2b; Mint debit/credit parser done | needs the account map, the vendor profiles and the replay tests; PR after P2b merges |
| P9b-link-3 (Prism) | not started | Bulwark lows on #99 (LinkFlow unmount, first-`/auth/me` 401, generation check before open) plus Prism polish (focus target, rAF cancel) |
| #67, #16, #8, #36 | older | #8 is superseded by this checkpoint's setup files (reconcile and close); #36 (Node 26) needs a toolchain decision |

## Design rulings this sprint (Keel)

- **Bus stays plain YAML.** An encoded dialect was declined: audits and leak scans need readable text.
- **Regex rules (#92):** after six evasion rounds, a strict allowlist grammar parsed by our own scanner. Structural budgets: total counted repetition ≤ 8 (literal counts free), class positions ≤ 24, programsize ≤ 2000. No inline flags. NFKC only for rule matching. A fail-closed probe, and a differential test against RE2 in CI.
- **P2b:** near-edge pairing needs `authorized_date` evidence. A per-row currency check. Matching waits for complete history. Flagged blocks aren't matched. Covered pending rows with user metadata are kept and counted.
- **I3:** an explicit `account_map` on every multi-account import. No stored maps (I3b later). Mint direction column in scope. Source categories not mapped.

## Owner queue

- Whether an acting lead may merge an already-gated PR while Claude is out (default: no).
- ADR 0039 hardening options: lock the private directory when idle, an `inotifywait` read log, `tailscale set --operator=`.
- #36 Node 24 → 26.

## Tooling notes

- The network dropped twice this sprint. Reviews ran on local SHAs and pushes waited. `scratchpad/land.sh` (update branch, prove the PR files are unchanged since approval or changed only by main, label, wait, squash with `--match-head-commit`) serialises merges so they don't race on `main`.
- zsh: `"$b:refs/..."` expands `:r` as a modifier. Write `"${b}:refs/heads/${b}"`.
- Grep-and-replace in a worktree must exclude the `.git` gitfile (`--exclude=.git`), not just `.git/` directories.
