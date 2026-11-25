# AGENTS.md — Holdfast

Read [the shared autonomous engineering policy](docs/process/AUTONOMOUS-ENGINEERING.md).
Apply it through Holdfast’s existing agent lanes, security rules and SDLC.

Follow `CLAUDE.md` for engineering rules and commands. Always read
`.claude/rules/security.md`. Before reviewing or editing a path, read every
matching rule in `.claude/rules/` using its `paths` frontmatter. Codex uses
the skills under `.agents/skills/` and the profiles under `.codex/agents/`.
Grok uses `.grok/skills/` and `.grok/agents/` (same skill names, current SDLC).
Use
`docs/STATUS.md` for phase progress and `docs/process/SDLC.md` for the current
story and merge workflow.

Use the [triad protocol](docs/process/TRIAD.md)
for cross-team messages, usage handoffs, and release coordination.

Claude Code remains the project engineer. Codex works as **Anvil**. For setup
and repository review, Anvil may review tracked project files and edit this
file, Codex configuration under `.codex/` and `.agents/`, and its setup report
in `docs/process/`. Report setup work in the owner conversation.

For assigned stories, follow ADR 0012 and `docs/planning/HANDOFF.md` where
available. Coordinate in the handoff queue and limit edits to the story brief.
Protected paths and migrations require the existing reviewer and migration-token
rules. Keel alone merges and releases. Keep live Plaid, private data, secret
files, deployments, and owner-queue actions outside Anvil's lane.

See `docs/process/CODEX-SETUP.md` for the Codex configuration and coverage.
Treat `CLAUDE.md` and `.claude/` as read-only during Codex work.

Grok works as **Helm**. Claude's Keel remains the organizer: only Keel merges,
tags, and releases. Helm may review tracked project files and edit Grok
configuration under `.grok/`, this lane section, and `docs/process/GROK-SETUP.md`.
Helm uses `.grok/agents/`, `.grok/skills/`, `.grok/workflows/holdfast-review.rhai`,
and `.grok/hooks/`. The roster lanes apply. Live Plaid, private data, secret
files, deployments, and owner-queue actions stay outside Helm's lane. Treat
`CLAUDE.md` and `.claude/` as read-only during Helm work too.

Helm builds full-stack on its own branches per ADR 0039 and `docs/process/TRIAD.md`.
Both Anvil and Helm push with the repo hooks (`core.hooksPath` is `.githooks`) and
never use `--no-verify`.

See `docs/process/GROK-SETUP.md`. Define a lane here before another agent or
vendor starts work.
