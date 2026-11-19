# Model routing and token budget

Keel (the main session) picks the model for every run. The goal: the most verified work per token, without stalling on usage limits.

## Routing rules (Keel applies these per run)

| Work | Model · effort | Why |
|---|---|---|
| Search, inventories, "where is X", link checks, log triage | **haiku** · low (Scout / `Explore`) | Cheap and fast; returns conclusions, not file dumps |
| Well-specified story with a contract, tests named, files listed | **sonnet** · medium | The bulk of build work |
| Ambiguous design, new ADR, cross-cutting refactor, migration with RLS | **opus** · high (Lattice) | Wrong design is the most expensive bug |
| Security review, crypto custody, redaction, tax-figure verification | **opus** · high | Blocking gates; false negatives are costly |
| Money review, acceptance, UI/brand review | **sonnet** · high | Checklist-driven, evidence-based |
| Docs, release notes, copy, site tweaks | **sonnet** · low (Beacon) | Low risk |
| Debugging after one failed sonnet attempt | **opus** · high | Escalate once instead of looping |

**Escalate** sonnet → opus when a run fails verification twice, touches auth/crypto/redaction/RLS, or needs a decision not in the contract.
**Downgrade** opus → sonnet once a design is written down as a contract; to haiku for anything that is pure lookup.
Agent frontmatter (`model:`, `effort:`) sets the default; Keel passes `model` on the Agent call to override.

## Token savers (mandatory)

1. **Brief by file.** Keel writes the story brief to `docs/planning/briefs/<story>.md` (or the scratchpad) and the run reads it. Resumes and retries point at the same file instead of re-sending context.
2. **Contracts before code.** Lattice writes the phase contract once (`docs/phases/PHASE-<n>-contract.md`); every run reads only its section.
3. **Scoped reading.** Runs read the files listed in their brief, use `grep`/`sed -n` ranges, and never read whole large files (PLAN.md is 1,179 lines: read sections by line range).
4. **Short reports.** Every run ends with ≤ 200 words: branch, commits, verification lines, open issues. No recaps of the brief.
5. **Verify once, at the end.** Targeted tests while iterating; the full `make test && make lint` once before committing.
6. **Stable prompt prefixes.** Same brief header and agent definitions across runs, so prompt caching hits (Phase 0–1: 235M cache-read vs 6.6M cache-write tokens).
7. **No duplicate work.** One owner per path (ROSTER lanes). Reviews read the diff, not the repo.
8. **Worktree runs start with** `git merge --ff-only <phase branch>` (stale-base bug).
9. **Parallelism cap:** 3 build runs + 1 review per 5-hour window. Four or more parallel runs hit the limit on 2026-11-18.
10. **Checkpoint before limits.** Keel updates `docs/STATUS.md` "Resume here" after every merge, so a limit reset or new session resumes in one read.

## Budget units

| Unit | Meaning | Measured basis |
|---|---|---|
| Run | One subagent story to a verified commit | Phase 0–1: 28 runs |
| S / M / L | 1 / 2–3 / 4–6 runs ≈ 80k / 250k / 600k tokens processed | 80–170k per run observed |
| Window | One 5-hour usage window ≈ 3–4 runs in parallel ≈ 1.5–2M tokens | Limit hit with 3 builders + reviews |
| Wall clock | 3–8 min per run; a window clears ~8–12 runs if chained | Hardening run: 5 min, 164k |

Keel logs actual tokens per run in the sprint log (`docs/planning/SPRINTS.md`) and recalibrates sizes each sprint.
