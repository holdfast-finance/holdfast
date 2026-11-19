---
name: adr
description: Write a Holdfast Architecture Decision Record. Use whenever a non-obvious technical decision is made: choosing a library or service, deviating from PLAN.md, adding an external dependency or service (required by hard rule 9), or settling a trade-off.
---

# ADR

1. Next number: `ls docs/adr | sort | tail -1` → increment (4 digits).
2. File: `docs/adr/NNNN-kebab-title.md` using this template:

```markdown
# NNNN. <Title>

- Status: Accepted | Proposed | Superseded by NNNN
- Date: YYYY-MM-DD
- Deciders: project engineer (Claude Code); owner review at phase end

## Context
What forces are at play. Cite PLAN.md sections and research where relevant.

## Decision
What we're doing, stated plainly.

## Alternatives considered
- Option: why not (one line each)

## Consequences
What gets easier, what gets harder, what we must now do (follow-ups).
```

3. If it supersedes an older ADR, update the old one's Status line.
4. If it changes a hard rule or a command, update `CLAUDE.md` in the same commit.
5. Commit: `docs(adr): NNNN <title>`.
