# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-11-18
- Deciders: project engineer (Claude Code)

## Context
Holdfast is built phase by phase by Claude Code acting as project engineer, with owner review at phase boundaries. Decisions need to survive across sessions and context resets.

## Decision
Record every non-obvious technical decision as an ADR in `docs/adr/`, using the `adr` skill. Adding an external service requires an ADR (CLAUDE.md hard rule 9).

## Consequences
New sessions read ADRs instead of reopening settled questions. Superseded ADRs stay, marked as superseded.
