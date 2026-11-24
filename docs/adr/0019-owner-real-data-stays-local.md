# 0019. Agents may read the instance owner's real statements locally; real data never leaves the machine

- Status: Accepted (amended by 0039)
- Date: 2026-11-20
- Deciders: Owner; Keel

## Context
Until now no agent could read a real statement. The owner ran `make sanitize`, reviewed the
output, and agents built bank profiles from that output only (`docs/ops/statement-sanitizer.md`,
`.claude/rules/security.md`). That was a team rule, not something the owner had asked for.

On 2026-11-20 the owner handed over their statements and overruled it: Holdfast is first an
application for the owner, built on the owner's machine, and the statements are the only source of
real history until Plaid is connected. The owner's one condition is that private data is never
pushed or published.

Building a parser against sanitized text has a real cost. The sanitizer rewrites every word and
amount, so it can hide exactly the layout quirks a profile has to handle, and a statement that
reconciles after sanitizing proves nothing about the original.

## Decision
1. On the instance owner's own machine, Holdfast's agents may read the owner's real statements and
   exports to build and test importers and to load the owner's local instance.
2. Real data lives in a private directory **outside the repository** (mode `0700`, files `0600`).
   It is never copied under the repo tree, so no `git add`, Docker build context, `uv build` or
   release job can pick it up. `data/` inside the repo stays git-ignored and stays denied to agent
   reads; it is the running app's document store, not a drop folder.
3. Real data never enters a commit, a PR or issue, a log line, a fixture, an image, a wheel or
   sdist, a release, or any service or tool outside the machine. That includes other vendors'
   coding CLIs (Codex, Grok): they keep working in clean worktrees with no real data.
4. Anything derived from real data is real data: extracted text, parser output, golden or
   snapshot files, test IDs, database dumps, screenshots of an instance loaded with it, and any
   regex or profile that embeds a name or account digits. Agents report on real data in
   aggregate: file counts, statement families, reconcile pass rates, parser errors by type. No
   names, account numbers, merchants or amounts in reports, commit messages or PR text.
5. Hard rule 7 is unchanged. Repo fixtures are synthetic or sanitizer output. The sanitizer is now
   the tool that turns a real statement into a committable fixture, not a gate in front of agents.
6. Hard rules 3 and 13 are unchanged: nothing from a statement is logged, and nothing about the
   owner (institutions held, state, names) is written into code or docs. A bank profile describes
   an institution's public statement layout, not who banks there.

## Consequences
- Profiles can be developed and reconciled against the real layouts, then proven again in CI
  against sanitized fixtures.
- Statement text passes through the model the agents run on. The owner accepted that. It is the
  same trust decision as choosing the advisor's provider in Phase 8, made earlier.
- The protection is placement plus discipline, with backstops: `.gitignore`, `.dockerignore`,
  gitleaks, the guard hook, a pre-commit check that refuses statement-type files and greps the
  staged diff against a private denylist kept outside the repo, and a CI check that fails on any
  tracked-but-ignored file or statement-type file outside the fixture allowlist. A reviewer must
  reject any fixture that is not sanitizer output.
- This is a decision by one instance owner about their own data. It grants nothing over anyone
  else's data and does not change what Holdfast Cloud may do.

## Alternatives rejected
- **Keep sanitize-then-review.** Safer against agent error, but the owner declined it and it hides
  parser bugs.
- **Lift the `Read(./data/**)` deny and use `data/inbox/`.** Keeps everything in one tree, but
  puts real PDFs one mistaken `git add -f` or build-context change away from publication.
